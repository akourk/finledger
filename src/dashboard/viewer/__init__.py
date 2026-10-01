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
    template = template.replace('Portfolio Dashboard', 'Local snapshot dashboard')
    config = {'template': template, 'start': start - program_start, 'end': end - program_start}
    html = html[:match.start()] + html[match.start():].replace('<script>', '<script id="finledger-renderer">', 1)
    control = '''<section id="snapshot-controls" aria-label="Local snapshot viewer">
<p>Open an exported FinLedger viewer snapshot. It stays in this page’s memory; nothing is uploaded or saved. Reloading returns to the fictional demo.</p>
<div class="snapshot-actions"><label class="tbtn" for="snapshot-file">Open your snapshot</label><input id="snapshot-file" type="file" accept=".json,application/json"><button id="snapshot-reset" class="tbtn" type="button" hidden>Return to demo</button></div>
<p id="snapshot-drop">Or drop one viewer JSON file here (up to 25 MiB). Raw broker CSVs and backup snapshots are not supported.</p>
<p id="snapshot-status" role="status" aria-live="polite">Showing the fictional demo.</p><p id="snapshot-error" role="alert" hidden></p>
</section><div id="snapshot-host"></div>'''
    style = (ASSETS / 'viewer.css').read_text(encoding='utf-8')
    controller = (ASSETS / 'viewer.js').read_text(encoding='utf-8')
    html = html.replace('</head>', '<style id="snapshot-styles">' + style + '</style></head>', 1)
    html = html.replace('<div class="top-bar">', control + '\n<div class="top-bar">', 1)
    return html.replace('</body>', '<script id="snapshot-config" type="application/json">' + script_json(config) + '</script>\n<script>' + controller + '</script>\n</body>', 1)
