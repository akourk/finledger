"""The offline publisher and browser agree on the versioned viewing boundary."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from src.viewer_snapshot import validate_viewer_snapshot


def test_python_and_browser_accept_the_same_viewer_files():
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node is required for viewer validation parity")
    data = {
        "as_of": "2026-06-30", "generated": "2026-06-30T00:00:00",
        "count": 1,
        "transactions": [{"date": "2026-06-01", "symbol": "FICTION",
                          "quantity": 2, "price": 12.5, "amount": 25,
                          "description": "Example's \"quoted\" & Unicode café"}],
        "holdings": [{"symbol": "FICTION", "quantity": 2, "value": 25}],
        "holdings_by_account": [],
        "history": [{"date": "2026-06-30", "total": 25}],
        "action_catalog": {"actions": [{"name": "Buy", "color": "#4ade80"}]},
        **{key: {} for key in ("analytics", "basis_totals", "basis_methods",
                              "cash_summary", "retirement_meta", "sector_of",
                              "display_of", "tax_tables")},
    }
    original = {"format": "finledger-viewer", "version": 1,
                "as_of": "2026-06-30", "data": data}
    cases = [("valid punctuation and values", original, True)]

    def changed(label, path, value, expected=False):
        snapshot = copy.deepcopy(original)
        target = snapshot
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        cases.append((label, snapshot, expected))

    for version in (True, 0, 2, "1", None):
        changed(f"version {version!r}", ["version"], version)
    for date in ("0000-01-01", "2026-02-30", "2025-02-29", "2026-6-30", ""):
        changed(f"history date {date}", ["data", "history", 0, "date"], date)
    changed("leap date", ["data", "transactions", 0, "date"], "2024-02-29", True)
    changed("date mismatch", ["data", "as_of"], "2026-06-29")
    changed("record array", ["data", "transactions"], [[]])
    changed("missing object", ["data", "tax_tables"], None)
    changed("mismatched count", ["data", "count"], 9)
    changed("boolean amount", ["data", "transactions", 0, "amount"], True)
    changed("string quantity", ["data", "holdings", 0, "quantity"], "2")
    changed("object label", ["data", "transactions", 0, "description"], {})
    for field in ("balance", "realized_gain", "cost_basis", "value", "price"):
        changed(f"nullable {field}", ["data", "transactions", 0, field], None, True)
    changed("markup label", ["data", "transactions", 0, "description"], "<img>")
    changed("nested markup count", ["data", "analytics"],
            {"reconciliation": {"summary": {"off": "<svg onload=alert(1)>"}}})
    changed("prototype key", ["data", "analytics"], {"__proto__": {}})
    changed("markup key", ["data", "sector_of"], {"<img>": "Example"})
    changed("attribute injection", ["data", "action_catalog", "actions", 0, "color"],
            'red" onpointerover="window.injected=true" data-x="')
    changed("short hex", ["data", "action_catalog", "actions", 0, "color"], "#abc")
    changed("action name", ["data", "action_catalog", "actions", 0, "name"], 42)
    changed("uppercase hex", ["data", "action_catalog", "actions", 0, "color"], "#ABCDEF", True)
    nested = {}
    for _ in range(65):
        nested = {"next": nested}
    changed("excessive depth", ["data", "analytics"], nested)

    python_results = []
    for label, snapshot, expected in cases:
        try:
            validate_viewer_snapshot(snapshot)
            accepted = True
        except ValueError:
            accepted = False
        assert accepted == expected, label
        python_results.append(accepted)

    # Exercise the actual pure validation boundary, not a second test-written
    # validator. Controller wiring and file-size behavior have browser tests.
    controller = Path("src/dashboard/viewer/viewer.js").read_text(encoding="utf-8")
    start = controller.index("  const fail = ")
    end = controller.index("  function discardPending()")
    program = """
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({});
vm.runInContext(input.source + '\\nthis.validate = validate;', context);
process.stdout.write(JSON.stringify(input.cases.map(snapshot => {
  try { context.validate(snapshot); return true; } catch (_) { return false; }
})));
"""
    result = subprocess.run([node, "-e", program], text=True, capture_output=True,
                            input=json.dumps({"source": controller[start:end],
                                              "cases": [c[1] for c in cases]}),
                            timeout=30, check=True)
    browser_results = json.loads(result.stdout)
    for case, python_result, browser_result in zip(cases, python_results, browser_results):
        assert browser_result == python_result, case[0]
