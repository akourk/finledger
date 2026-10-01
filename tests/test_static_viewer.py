"""Public-only packaging and fictional browser-boundary validation."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from src.dashboard import generate_dashboard
from src.dashboard.viewer import inject_viewer
from tools.demo_banner import inject

ROOT = Path(__file__).resolve().parents[1]


def fictional_snapshot():
    data = {key: [] for key in ('transactions', 'holdings', 'holdings_by_account', 'history')}
    data.update({key: {} for key in ('analytics', 'basis_totals', 'basis_methods', 'cash_summary',
        'retirement_meta', 'sector_of', 'display_of', 'action_catalog', 'tax_tables')})
    data.update(as_of='2026-06-30', generated='2026-06-30T00:00:00', count=1,
        transactions=[dict(date='2026-06-30', amount=17.5, balance=None, realized_gain=None,
            description='Fictional O\'Brien "quoted" & Ω')])
    data['action_catalog'] = {'actions': [{'name': 'Fictional', 'color': '#abcdef'}]}
    return {'format': 'finledger-viewer', 'version': 1, 'as_of': '2026-06-30', 'data': data}


def test_public_packaging_reuses_renderer_without_data_or_code_duplication(tmp_path):
    source, output = tmp_path / 'fictional.json', tmp_path / 'fictional.html'
    source.write_text(json.dumps(fictional_snapshot()['data']), encoding='utf8')
    generate_dashboard(source, output)
    original = output.read_text(encoding='utf8')
    public = inject(original)
    assert 'snapshot-controls' not in original
    assert public.count('const DATA = ') == 1
    assert public.count('function activateTab(') == 1
    config_text = public.split('<script id="snapshot-config" type="application/json">')[1].split('</script>')[0]
    config = json.loads(config_text)
    assert 'const DATA' not in config['template']
    assert '<script' not in config['template']
    assert '__VIEWER_SCRIPT__' in config['template']
    assert 'topBarRefresh' not in config['template']
    assert 'demo-intro' not in config['template']
    assert 'allow-scripts allow-downloads' in public
    assert 'allow-same-origin' not in public
    assert "connect-src 'none'" in public
    assert "script-src-attr 'unsafe-inline'" in public
    assert public.index('class="skip-link"') < public.index('id="demo-intro"')
    assert inject(public) == public


def test_small_banner_fixture_does_not_require_a_renderer():
    assert inject_viewer('<html><body>Fictional</body></html>') == '<html><body>Fictional</body></html>'


CASES = [
    ('normal', True), ('raw', False), ('version', False), ('boolean_version', False),
    ('invalid_date', False), ('year_zero', False), ('markup', False), ('markup_key', False),
    ('prototype', False), ('quote_color', False), ('short_color', False),
    ('color_name', False), ('string_amount', False), ('bool_amount', False),
    ('bad_description', False), ('count', False), ('missing', False),
    ('deep', False), ('nested_position', False), ('breakdown', False), ('date_mismatch', False),
]


def boundary_case(name):
    snapshot = deepcopy(fictional_snapshot())
    data = snapshot['data']
    if name == 'raw': return {'format': 'finledger-snapshot', 'files': {}}
    if name == 'version': snapshot['version'] = 2
    if name == 'boolean_version': snapshot['version'] = True
    if name == 'invalid_date': data['transactions'][0]['date'] = '2026-02-30'
    if name == 'year_zero': snapshot['as_of'] = data['as_of'] = '0000-01-01'
    if name == 'markup': data['analytics']['reconciliation'] = {'summary': {'off': '<img src=x>'}}
    if name == 'markup_key': data['display_of']['<svg>'] = 'Fictional'
    if name == 'prototype': data['analytics']['__proto__'] = {}
    if name == 'quote_color': data['action_catalog']['actions'][0]['color'] = 'red" onpointerover="alert(1)'
    if name == 'short_color': data['action_catalog']['actions'][0]['color'] = '#fff'
    if name == 'color_name': data['action_catalog']['actions'][0]['name'] = 3
    if name == 'string_amount': data['transactions'][0]['amount'] = '17.5'
    if name == 'bool_amount': data['transactions'][0]['amount'] = True
    if name == 'bad_description': data['transactions'][0]['description'] = {}
    if name == 'count': data['count'] = 0
    if name == 'missing': del data['analytics']
    if name == 'deep':
        node = data['analytics']
        for _ in range(65): node['next'] = {}; node = node['next']
    if name == 'nested_position': data['history'] = [{'date': '2026-06-30', 'positions': [{'value': '17.5'}]}]
    if name == 'breakdown': data['history'] = [{'date': '2026-06-30', 'by_account_group': {'Fictional': True}}]
    if name == 'date_mismatch': snapshot['as_of'] = '2026-07-01'
    return snapshot


@pytest.mark.parametrize('name,accepted', CASES)
def test_actual_browser_validator_boundaries(name, accepted):
    # Execute the shipped validator in a pure JS VM: no browser or network APIs.
    script = r'''
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const code=source.slice(source.indexOf('  const fail ='),source.indexOf('  function discardPending'));
const snapshot=JSON.parse(fs.readFileSync(0,'utf8'));
try {vm.runInNewContext(code+'\nvalidate(snapshot)',{snapshot});process.stdout.write('accepted');}
catch (_) {process.stdout.write('rejected');}
'''
    result = subprocess.run(['node', '-e', script, str(ROOT / 'src/dashboard/viewer/viewer.js')],
        input=json.dumps(boundary_case(name)), capture_output=True, text=True, check=True)
    assert result.stdout == ('accepted' if accepted else 'rejected')


def test_controller_mobile_shell_and_reset_keep_accessible_controls(tmp_path):
    """Drive actual controller events without a DOM renderer or network APIs."""
    source, output = tmp_path / 'fictional.json', tmp_path / 'fictional.html'
    source.write_text(json.dumps(fictional_snapshot()['data']), encoding='utf8')
    generate_dashboard(source, output)
    html = inject(output.read_text(encoding='utf8'))
    config = json.loads(html.split('<script id="snapshot-config" type="application/json">')[1].split('</script>')[0])
    assert 'viewer-active' not in config['template']
    assert 'snapshot-controls' not in config['template']
    assert 'Portfolio Dashboard' not in config['template']
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const elements=new Map(), messages=[], listeners=new Map();let focused=null;
function element(id) {
  const names=new Set(),events=new Map();
  return {id,textContent:'',hidden:false,open:false,files:[],value:'',href:'',dataset:{},
    classList:{add:n=>names.add(n),remove:n=>names.delete(n),contains:n=>names.has(n)},
    addEventListener:(name,callback)=>events.set(name,callback),emit:name=>events.get(name)?.(),
    focus(){focused=this.id;},click(){this.clicks=(this.clicks||0)+1;this.emit('click');},
    setAttribute(){},append(child){this.child=child;},remove(){this.removed=true;},
    contentWindow:{postMessage:(data,target)=>messages.push({data,target})}};
}
for (const id of ['snapshot-open','snapshot-help','snapshot-file','snapshot-reset','snapshot-host',
 'snapshot-status','snapshot-error','snapshot-controls','demo-intro','tabnav','main-content',
 'mobile-navigation','mobile-sections','body','skip','topbar','snapshot-config','finledger-renderer']) elements.set(id,element(id));
elements.get('snapshot-config').textContent=JSON.stringify(input.config);
elements.get('finledger-renderer').textContent='const DATA = {};';
const document={getElementById:id=>elements.get(id),body:elements.get('body'),
 querySelector:selector=>elements.get(selector==='.skip-link'?'skip':'topbar'),createElement:()=>element('iframe')};
const sandbox={document,window:{addEventListener:(name,fn)=>listeners.set(name,fn)},
 crypto:{getRandomValues:bytes=>bytes.fill(1)},setTimeout:()=>1,clearTimeout:()=>{}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),sandbox);
async function select(value) {
 elements.get('snapshot-file').files=[{size:100,text:async()=>JSON.stringify(value)}];
 elements.get('snapshot-file').emit('change');await new Promise(resolve=>setImmediate(resolve));
}
(async()=>{
 elements.get('snapshot-open').click();assert.equal(elements.get('snapshot-file').clicks,1);
 await select(input.snapshot);
 const frame=elements.get('snapshot-host').child;
 listeners.get('message')({source:{},data:{type:'finledger-viewer-ready'}});
 assert.equal(document.body.classList.contains('viewer-active'),false,'untrusted message ignored');
 listeners.get('message')({source:frame.contentWindow,data:{type:'finledger-viewer-ready'}});
 assert.equal(document.body.classList.contains('viewer-active'),true);
 assert.equal(elements.get('snapshot-open').textContent,'Change file');
 for(const id of ['mobile-navigation','mobile-sections','main-content']) assert.equal(elements.get(id).classList.contains('viewer-demo-hidden'),true);
 assert.equal(elements.get('skip').href,'#snapshot-host');
 assert.deepEqual(JSON.parse(JSON.stringify(messages)),[{data:{type:'finledger-viewer-visible'},target:'*'}],'only fixed lifecycle message, no financial fields');
 await select({format:'unsupported'});assert.equal(frame.removed,undefined,'invalid file keeps current view');
 elements.get('snapshot-reset').click();assert.equal(frame.removed,true);
 assert.equal(document.body.classList.contains('viewer-active'),false);
 assert.equal(elements.get('snapshot-open').textContent,'Open snapshot');assert.equal(focused,'snapshot-open');
 assert.equal(elements.get('snapshot-file').value,'');assert.equal(elements.get('skip').href,'#main-content');
 for(const id of ['mobile-navigation','mobile-sections','main-content']) assert.equal(elements.get(id).classList.contains('viewer-demo-hidden'),false);
 process.stdout.write('passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', script, str(ROOT / 'src/dashboard/viewer/viewer.js')],
        input=json.dumps({'config': config, 'snapshot': fictional_snapshot()}), capture_output=True, text=True, check=True)
    assert result.stdout == 'passed'
