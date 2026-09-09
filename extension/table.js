import { COLUMNS, tableRows, toCsv } from './src/table.js';
import { clampConfig } from './src/core.js';
let records = [];
async function refresh() {
  try {
    const stored = await chrome.storage.local.get(['history', 'lastResult', 'config']);
    records = Array.isArray(stored.history) ? stored.history : [];
    if (!records.length && stored.lastResult) records = [stored.lastResult];
    const table = document.getElementById('data');
    table.replaceChildren();
    const header = document.createElement('thead');
    const headerRow = document.createElement('tr');
    for (const [label] of COLUMNS) { const cell = document.createElement('th'); cell.textContent = label; headerRow.append(cell); }
    header.append(headerRow); table.append(header);
    const body = document.createElement('tbody');
    for (const values of tableRows(records)) {
      const row = document.createElement('tr');
      for (const value of values) { const cell = document.createElement('td'); cell.textContent = String(value); row.append(cell); }
      body.append(row);
    }
    table.append(body);
    document.getElementById('summary').textContent = `共 ${records.length} 条记录，当前最多保留最近 ${clampConfig(stored.config).maxHistory} 次采集（所有门店合计）。超出后最旧记录会移除，请及时导出。`;
    document.getElementById('export').disabled = !records.length;
  } catch { document.getElementById('summary').textContent = '读取本地记录失败，请重新打开此页面。'; }
}
document.getElementById('refresh').addEventListener('click', refresh);
document.getElementById('export').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([toCsv(records)], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a'); a.href = url;
  a.download = `携程采集记录_${new Date().toISOString().replaceAll(':','-').replaceAll('.','-')}.csv`;
  a.click(); setTimeout(() => URL.revokeObjectURL(url), 60000);
});
chrome.storage.onChanged.addListener((changes, area) => { if (area === 'local' && (changes.history || changes.lastResult)) void refresh(); });
void refresh();
