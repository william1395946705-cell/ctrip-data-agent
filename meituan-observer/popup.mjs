const $=id=>document.getElementById(id);
let report=null;
const errors={ordinary_page_required:'请停留在已登录美团首页或订单页。',identity_missing:'尚无成对门店标识，请手动刷新当前普通页面，再检查状态。',identity_conflict:'发现多组门店标识，已停止。请确认门店后手动刷新普通页面。',identity_changed:'采集期间门店上下文变化，结果已丢弃。',page_changed:'页面发生变化，测试已停止。',cooldown:'两次测试请间隔30秒。',in_progress:'测试正在进行。',timeout:'测试超时。',browser_connection_failed:'页面连接失败，请重新打开弹窗。'};
function showReport(r) {
  report=r;$('export').disabled=!r;
  if(!r)return;
  const failed=r.results?.find(x=>x.status!=='data_received_unverified');
  $('last').textContent=(r.started_at?new Date(r.started_at).toLocaleString():'')+' · '+
    (r.received_all_cards?'已收到数据，门店和日期仍需核对':failed?.business_status===33202?'商家鉴权失败（33202）':failed?.status||'未完成');
}
async function send(action) {
  try{return await chrome.runtime.sendMessage({action});}catch{return {ok:false,error:'browser_connection_failed'};}
}
async function refresh() {
  $('collect').disabled=true;
  const r=await send('status');
  if(!r?.ok){$('identity').textContent=errors[r?.error]||'无法读取当前页面';return;}
  const state=r.value.identity_state;
  $('identity').textContent=state==='candidate'?'已找到一组门店标识候选，等待数据核对。':errors['identity_'+state]||'页面未就绪';
  $('collect').disabled=state!=='candidate'||r.value.running;
  showReport(r.value.last);
}
$('refresh').onclick=refresh;
$('collect').onclick=async()=>{
  $('collect').disabled=true;$('status').textContent='正在当前页面执行只读测试…';
  const r=await send('collect');
  if(r?.ok){showReport(r.value);$('status').textContent=r.value.received_all_cards?'收到数据，请核对门店与昨日日期。':'测试未取得完整数据，请导出报告。';}
  else $('status').textContent=errors[r?.error]||'测试未完成';
  await refresh();
};
$('export').onclick=()=>{
  if(!report)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='meituan-popup-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
refresh();
