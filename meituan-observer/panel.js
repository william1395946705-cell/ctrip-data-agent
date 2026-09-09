import {targetEndpoint, safeRequest, responseStructure} from './sanitize.mjs';
import {dataCenterPage, ordinaryPage, prepareTemplate, validateSet, pageRequest} from './replay.mjs';
import {directProbe, safeDirectReport} from './direct.mjs';

const $ = id => document.getElementById(id);
let running = false;
let generation = 0;
let accepted = 0;
let records = [];
let templates = new Map();
let replayReport = null;
let replayInFlight = false;
let replayConsumed = false;

function setText(id, value) { $(id).textContent = value; }
function inspect(expression) {
  return new Promise(resolve => chrome.devtools.inspectedWindow.eval(expression, (value, error) => {
    resolve(error ? {error: 'page_eval_failed'} : {value});
  }));
}
async function pageInfo() {
  const result = await inspect('({href:location.href,origin:location.origin,focus:document.hasFocus()})');
  return result.error ? null : result.value;
}
function templateList() { return [...templates.values()]; }
function updateTemplateView() {
  const list = templateList();
  setText('templates', list.length ? list.map(t => `${t.route} · ${t.baseline.length} cards · captured`).join('\n') : '尚未捕获目标接口。');
  const problem = list.length === 3 ? validateSet(list) : 'capture_three_endpoints_first';
  $('replay').disabled = replayInFlight || replayConsumed || !$('sameHotel').checked || Boolean(problem);
  if (problem) setText('replayStatus', problem === 'capture_three_endpoints_first' ? '需要先捕获三个目标接口。' : `Replay 暂不可用：${problem}`);
}
function captureStatus(text) { setText('captureStatus', text); }
function replayStatus(text) { setText('replayStatus', text); }
function resetCapture() {
  accepted = 0;
  records = [];
  templates = new Map();
  replayReport = null;
  replayConsumed = false;
  $('exportReplay').disabled = true;
  setText('replayResult', '');
  updateTemplateView();
}
async function startCapture() {
  if (replayInFlight) return;
  const epoch = ++generation;
  const info = await pageInfo();
  if (epoch !== generation) return;
  if (!info || info.origin !== 'https://me.meituan.com' || !dataCenterPage(info.href)) {
    running = false;
    captureStatus('请先手动打开美团数据中心的“昨日”页面，再开始捕获。脚本不会帮你导航。');
    return;
  }
  resetCapture();
  running = true;
  captureStatus('捕获已启动。现在请手动刷新数据中心昨日页面；三类目标请求会在本机内存中暂存。');
}
$('start').onclick = startCapture;
$('stop').onclick = () => {
  running = false;
  ++generation;
  captureStatus(`已停止捕获；目标接口 ${templates.size}/3。脱敏发现仍只在内存中。`);
  updateTemplateView();
};
$('sameHotel').onchange = updateTemplateView;

chrome.devtools.network.onNavigated.addListener(url => {
  let origin = '';
  try { origin = new URL(url).origin; } catch {}
  if (origin !== 'https://me.meituan.com') {
    running = false;
    ++generation;
    records = [];
    accepted = 0;
    templates = new Map();
    captureStatus('已离开美团后台，监听和临时接口模板已清空。');
    updateTemplateView();
    return;
  }
  if (running && !dataCenterPage(url)) {
    running = false;
    ++generation;
    captureStatus('已离开数据中心，捕获停止；请在确认同一家门店的普通页执行 Replay。');
  }
  updateTemplateView();
});

chrome.devtools.network.onRequestFinished.addListener(async entry => {
  if (!running || accepted >= 200 || !targetEndpoint(entry.request?.url)) return;
  const mime = String(entry.response?.content?.mimeType || '').toLowerCase();
  if (mime && !/json|text\/plain/.test(mime)) return;
  const epoch = generation;
  const info = await pageInfo();
  if (!info || !dataCenterPage(info.href) || !running || epoch !== generation) return;
  const record = safeRequest(entry);
  if (!record) return;
  accepted++;
  entry.getContent((content, encoding) => {
    if (!running || epoch !== generation) return;
    record.response_schema = responseStructure(content, encoding, entry.request.url);
    records.push(record);
    const samples = record.response_schema.business_samples;
    const template = prepareTemplate(entry, samples);
    if (template) templates.set(template.route, template);
    setText('templates', templateList().map(t => `${t.route} · ${t.baseline.length} cards · captured`).join('\n') || '尚未捕获目标接口。');
    captureStatus(`已捕获 ${templates.size}/3 个目标接口；页面请求仍由美团自然产生。`);
    if (templates.size === 3) {
      running = false;
      ++generation;
      captureStatus('三个目标接口已捕获。请手动切到同一家门店的普通页，再勾选确认执行一次 Replay。');
    }
    updateTemplateView();
  });
});

$('export').onclick = () => {
  const blob = new Blob([JSON.stringify({version:'0.2.0',mode:'passive_discovery',records}, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = 'meituan-discovery-sanitized.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

function randomKey() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return `__mt_probe_${[...bytes].map(x => x.toString(16).padStart(2, '0')).join('')}`;
}
function wait(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
async function pollProbe(key) {
  for (let i = 0; i < 180; i++) {
    const result = await inspect(`(()=>{const s=window[${JSON.stringify(key)}];if(!s||!s.done)return null;delete window[${JSON.stringify(key)}];return s.result;})()`);
    if (result.error) return {status:'page_eval_failed', current_page_unchanged:false, focus_unchanged:false};
    if (result.value) return result.value;
    await wait(100);
  }
  return {status:'probe_timeout', current_page_unchanged:false, focus_unchanged:false};
}
function safeReplayEntry(result, route) {
  return {
    route,
    status: typeof result?.status === 'string' ? result.status : 'request_failed',
    http_status: Number.isInteger(result?.http_status) ? result.http_status : null,
    business_status: Number.isSafeInteger(result?.business_status) ? result.business_status : null,
    card_count: Number.isInteger(result?.card_count) ? result.card_count : null,
    comparisons: Array.isArray(result?.comparisons) ? result.comparisons.slice(0, 20) : [],
    current_page_unchanged: result?.current_page_unchanged === true,
    focus_unchanged: result?.focus_unchanged === true
  };
}
async function runReplay() {
  if (replayInFlight || replayConsumed) return;
  const list = templateList();
  const problem = validateSet(list);
  if (problem) { replayStatus(`Replay 暂不可用：${problem}`); return; }
  const info = await pageInfo();
  if (!info || info.origin !== 'https://me.meituan.com' || !ordinaryPage(info.href)) {
    replayStatus('当前不是普通美团页面；请手动切换到首页、订单等普通页面。');
    return;
  }
  if (!$('sameHotel').checked) { replayStatus('请先勾选“同一家门店”确认。'); return; }
  replayInFlight = true;
  $('replay').disabled = true;
  replayStatus('Replay 已开始：按三个自然捕获 URL 顺序执行，只读请求中。');
  const expectedUrl = info.href;
  const report = {version:'0.2.0',mode:'controlled_replay',started_at:new Date().toISOString(),hotel_identity_verification:'manual_check_required',results:[]};
  for (const template of list) {
    const key = randomKey();
    const args = {expectedUrl, url:template.url, key, baseline:template.baseline};
    const expression = `(${pageRequest.toString()})(${JSON.stringify(args)})`;
    const started = await inspect(expression);
    let result;
    if (started.error || started.value !== 'started') result = {status:'page_eval_failed', current_page_unchanged:false, focus_unchanged:false};
    else result = await pollProbe(key);
    const safe = safeReplayEntry(result, template.route);
    report.results.push(safe);
    setText('replayResult', report.results.map(r => `${r.route} · ${r.status} · HTTP ${r.http_status ?? '—'}`).join('\n'));
    if (safe.status === 'page_changed') break;
  }
  const final = await pageInfo();
  report.current_page_unchanged = Boolean(final && final.href === expectedUrl && report.results.every(r => r.current_page_unchanged));
  report.focus_unchanged = Boolean(final && report.results.every(r => r.focus_unchanged));
  report.overall = report.results.length === 3 && report.results.every(r => r.status === 'match') && report.current_page_unchanged && report.focus_unchanged ? 'PASS' : 'NOT_VERIFIED';
  replayReport = report;
  replayConsumed = true;
  replayInFlight = false;
  $('exportReplay').disabled = false;
  replayStatus(report.overall === 'PASS' ? 'Replay PASS：三个接口值与捕获基线一致；页面和焦点未改变。' : 'Replay 未通过：请查看各端点状态；不把网络失败当作无数据。');
  updateTemplateView();
}
$('replay').onclick = runReplay;
$('exportReplay').onclick = () => {
  if (!replayReport) return;
  const blob = new Blob([JSON.stringify(replayReport, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = 'meituan-replay-report.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
updateTemplateView();

let directReport=null;
let lastDirect=0;
$('direct').onclick=async()=>{
  if(replayInFlight || Date.now()-lastDirect<30000)return;
  // Lock before asynchronous evaluation, including against legacy Replay clicks.
  replayInFlight=true;
  $('direct').disabled=true;
  try {
    const info=await pageInfo();
    if(!info || !ordinaryPage(info.href)) {setText('directStatus','请停留在已登录美团首页或订单页。');return;}
    running=false;++generation;templates=new Map();updateTemplateView();
    directReport=null;$('exportDirect').disabled=true;
    setText('directResult','');lastDirect=Date.now();
    setText('directStatus','正在测试当前会话，只发送已发现的三个只读接口，遇到错误会停止。');
    const key=randomKey();
    const started=await inspect(`(${directProbe.toString()})(${JSON.stringify({expectedUrl:info.href,key})})`);
    if(started.error || started.value!=='started') {setText('directStatus','无法在当前普通页启动测试。');return;}
    for(let i=0;i<500;i++) {
      const result=await inspect(`(()=>{const s=window[${JSON.stringify(key)}];if(!s?.done)return null;delete window[${JSON.stringify(key)}];return s.result;})()`);
      if(result.error)break;
      if(result.value) {
        directReport=safeDirectReport(result.value);
        $('exportDirect').disabled=false;
        setText('directResult',JSON.stringify(directReport,null,2));
        setText('directStatus',directReport.received_all_cards?'已返回17项数据，请核对门店和统计日期；尚未标记采集验证成功。':'直接测试未完成，请导出报告定位缺失参数或请求失败。');
        return;
      }
      await wait(100);
    }
    setText('directStatus','页面通信中断或超时，未取得有效报告。');
  } finally {
    replayInFlight=false;updateTemplateView();
    setTimeout(()=>{$('direct').disabled=false;},Math.max(0,30000-(Date.now()-lastDirect)));
  }
};
$('exportDirect').onclick=()=>{
  if(!directReport)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(directReport,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='meituan-direct-report.json';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
