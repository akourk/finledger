"""Public-only local viewer packaging; renderer code is shared with the demo."""
import json
from pathlib import Path
import re

ASSETS = Path(__file__).resolve().parent


def script_json(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False)
            .replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def inject_viewer(html):
    match = re.search(r'<script>\s*const DATA = ', html)
    # Small banner-only fixtures intentionally have no dashboard renderer.
    if not match:
        return html
    start = match.end()
    _, length = json.JSONDecoder().raw_decode(html[start:])
    end = start + length
    close = html.index('</script>', end)
    program_start = html.index('const DATA = ', match.start())
    template = html[:match.start()] + '__VIEWER_SCRIPT__' + html[close + len('</script>'):]
    template = re.sub(r'<button\b[^>]*\bid="topBarRefresh"[^>]*>.*?</button>', '', template, count=1, flags=re.S)
    template = template.replace('Portfolio Dashboard', 'Portfolio')
    config = {'template': template, 'start': start - program_start, 'end': end - program_start}
    html = html[:match.start()] + html[match.start():].replace('<script>', '<script id="finledger-renderer">', 1)
    control = '''<section id="snapshot-controls" aria-label="Local snapshot viewer">
<div class="snapshot-toolbar"><div class="snapshot-intro"><p id="snapshot-status" role="status" aria-live="polite">Showing the fictional demo.</p><p class="snapshot-privacy">Your snapshot stays on this device. No upload or browser storage.</p></div>
<details id="snapshot-menu" open><summary id="snapshot-menu-toggle">File <span aria-hidden="true">⌄</span></summary><div class="snapshot-actions"><button id="snapshot-open" class="tbtn" type="button">Open snapshot</button><input id="snapshot-file" class="snapshot-file-input" type="file" accept=".json,application/json" aria-label="Choose a FinLedger viewer snapshot" tabindex="-1"><button id="snapshot-reset" class="tbtn" type="button" hidden>Return to demo</button>
<details id="snapshot-help"><summary>Help</summary><div class="snapshot-help-content" tabindex="0" role="region" aria-label="Snapshot help"><p>Choose a FinLedger viewer JSON exported from FinLedger on your computer (version 1, up to 25 MiB).</p><p id="snapshot-drop">You can also drop one snapshot here. Broker CSVs and backup snapshots are not supported.</p><p>Nothing is uploaded or kept in browser storage. Change files or return to the demo to close this view. Reloading restores the fictional demo. CSV downloads save only when you request them.</p></div></details></div></details></div>
<p id="snapshot-error" role="alert" hidden></p>
</section><div id="snapshot-host" tabindex="-1"></div>'''
    style = (ASSETS / 'viewer.css').read_text(encoding='utf-8')
    controller = (ASSETS / 'viewer.js').read_text(encoding='utf-8')
    html = html.replace('</head>', '<style id="snapshot-styles">' + style + '</style></head>', 1)
    # Controls and host must remain direct body children for the full-height
    # private viewer; the portfolio header is independently collapsible.
    shell = '<details id="portfolioContext"'
    html = html.replace(shell, control + '\n' + shell, 1)
    return html.replace('</body>', '<script id="snapshot-config" type="application/json">' + script_json(config) + '</script>\n<script>' + controller + '</script>\n</body>', 1)
