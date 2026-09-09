import {allowedUrl, safeRequest, responseStructure} from './sanitize.mjs';
const $ = id => document.getElementById(id);
let running = false, generation = 0, accepted = 0, records = [];
function status(text) { $('status').textContent = text; }
function correctPage() {
  return new Promise(resolve => chrome.devtools.inspectedWindow.eval('location.origin', (origin, error) => resolve(!error && origin === 'https://me.meituan.com')));
}
$('start').onclick = async () => {
  const epoch = ++generation;
  if (!await correctPage()) { running = false; status('请在美团后台页面的开发者工具中打开本面板。'); return; }
  if (epoch !== generation) return;
  running = true; accepted = 0; records = []; $('list').textContent = '';
  status('监听已启动。现在请手动刷新数据中心页面；无需再次点击开始。');
};
$('stop').onclick = () => { running = false; ++generation; status(`已停止，保存了 ${records.length} 条脱敏记录。`); };
chrome.devtools.network.onNavigated.addListener(url => {
  try { if (new URL(url).origin === 'https://me.meituan.com') return; } catch {}
  running = false; ++generation; records = []; accepted = 0; $('list').textContent = ''; status('离开美团后台，监听停止，内存记录已清空。');
});
chrome.devtools.network.onRequestFinished.addListener(async entry => {
  if (!running || accepted >= 200 || !allowedUrl(entry.request?.url)) return;
  const mime = String(entry.response?.content?.mimeType || '').toLowerCase();
  if (mime && !/json|text\/plain/.test(mime)) return;
  const epoch = generation;
  if (!await correctPage() || !running || epoch !== generation || accepted >= 200) return;
  const record = safeRequest(entry);
  if (!record) return;
  accepted++;
  entry.getContent((content, encoding) => {
    if (!running || epoch !== generation) return;
    record.response_schema = responseStructure(content, encoding);
    records.push(record);
    $('list').textContent = records.map(r=>`${r.http_status} ${r.method} ${r.request_url} [${r.response_schema.type}]`).join('\n');
    status(`已记录 ${records.length} 条；最多接收200条。页面加载完成后可导出。`);
  });
});
$('export').onclick = () => {
  const blob = new Blob([JSON.stringify({version:'0.1.0',mode:'passive',records},null,2)],{type:'application/json'});
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = 'meituan-discovery-sanitized.json'; a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
