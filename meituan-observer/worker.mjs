import {observeIdentity,resolveIdentity} from './identity.mjs';
import {directProbe,safeDirectReport} from './direct.mjs';
import {ordinaryPage} from './replay.mjs';

const active=new Set(), lastAttempt=new Map();
async function currentTab() {
  const [tab]=await chrome.tabs.query({active:true,lastFocusedWindow:true});
  if(!tab?.id||!ordinaryPage(tab.url))throw new Error('ordinary_page_required');
  return tab;
}
async function candidates(tab) {
  const frames=await chrome.scripting.executeScript({target:{tabId:tab.id,allFrames:true},world:'MAIN',func:observeIdentity});
  const top=frames.find(f=>f.frameId===0);
  if(!top?.documentId)throw new Error('document_unavailable');
  return {binding:resolveIdentity(frames.map(f=>f.result)),documentId:top.documentId};
}
function pollSlot(key,consume=false) {
  const s=window[key];
  if(!s)return {lost:true};
  if(!s.done)return null;
  if(consume)delete window[key];
  return s.result;
}
async function status() {
  const tab=await currentTab();
  const {binding}=await candidates(tab);
  return {identity_state:binding.state,running:active.has(tab.id),last:(await chrome.storage.local.get('lastReport')).lastReport??null};
}
async function collect() {
  const tab=await currentTab();
  if(active.has(tab.id))throw new Error('in_progress');
  if(Date.now()-(lastAttempt.get(tab.id)||0)<30000)throw new Error('cooldown');
  active.add(tab.id);
  try {
    const {binding,documentId}=await candidates(tab);
    if(binding.state!=='candidate')throw new Error('identity_'+binding.state);
    const latest=await currentTab();
    if(latest.id!==tab.id||latest.url!==tab.url)throw new Error('page_changed');
    lastAttempt.set(tab.id,Date.now());
    const key='__mt_probe_'+crypto.randomUUID().replaceAll('-','');
    const target={tabId:tab.id,documentIds:[documentId]};
    const [started]=await chrome.scripting.executeScript({target,world:'MAIN',func:directProbe,args:[{expectedUrl:tab.url,key,identity:binding.identity}]});
    if(started?.result!=='started')throw new Error('page_changed');
    for(let i=0;i<210;i++) {
      await new Promise(r=>setTimeout(r,250));
      const [result]=await chrome.scripting.executeScript({target,world:'MAIN',func:pollSlot,args:[key,true]});
      if(result?.result?.lost)throw new Error('page_changed');
      if(!result?.result)continue;
      const report=safeDirectReport(result.result);
      report.identity_source='ordinary_resource_candidate';
      report.identity_verification='NOT_VERIFIED';
      report.omitted_parameters=['dataScope','runtime_verification_fields'];
      // Recheck observations after the round; any hotel/frame change invalidates it.
      const after=await candidates(tab);
      if(after.documentId!==documentId||after.binding.state!=='candidate'||
        after.binding.identity.poiId!==binding.identity.poiId||after.binding.identity.partnerId!==binding.identity.partnerId)
        throw new Error('identity_changed');
      await chrome.storage.local.set({lastReport:report});
      return report;
    }
    throw new Error('timeout');
  } finally {active.delete(tab.id);}
}
const safeErrors=new Set(['ordinary_page_required','document_unavailable','in_progress','cooldown','identity_missing','identity_conflict','page_changed','identity_changed','timeout']);
chrome.tabs.onRemoved.addListener(id=>{lastAttempt.delete(id);active.delete(id);});
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(sender.id!==chrome.runtime.id||sender.url!==chrome.runtime.getURL('popup.html'))return;
  const action=message?.action;
  if(!['status','collect'].includes(action))return;
  (action==='status'?status():collect()).then(value=>reply({ok:true,value}),e=>reply({ok:false,error:safeErrors.has(e?.message)?e.message:'browser_connection_failed'}));
  return true;
});
