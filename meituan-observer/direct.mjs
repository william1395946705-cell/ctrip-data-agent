import {targetSamples} from './sanitize.mjs';
import {exactRoute, ROUTES} from './replay.mjs';
import {safeDiagnostics} from './diagnostics.mjs';

// Page-world results are untrusted: reconstruct the export from allowlists.
export function safeDirectReport(value) {
  const statuses=new Set(['merchant_authorization_failed','request_failed','redirect_blocked','login_expired','blocked','business_failed','response_contract_mismatch','shape_mismatch','data_received_unverified','data_incomplete','timeout','network_or_parse_failed']);
  const results=(Array.isArray(value?.results)?value.results:[]).slice(0,3).flatMap(r=>{
    if(!r || !Object.hasOwn(ROUTES,r.route) || !exactRoute('https://eb.meituan.com'+r.route))return [];
    const samples=targetSamples({data:{cards:Array.isArray(r.cards)?r.cards:[]}});
    return [{route:r.route,status:statuses.has(r.status)?r.status:'request_failed',
      http_status:Number.isInteger(r.http_status)&&r.http_status>=0&&r.http_status<=599?r.http_status:null,
      business_status:Number.isSafeInteger(r.business_status)&&Math.abs(r.business_status)<100000?r.business_status:null,
      diagnostics:safeDiagnostics(r.diagnostics),cards:samples.cards||[]}];
  });
  return {version:'0.3.2',mode:'direct_session_probe',overall:'NOT_VERIFIED',capture_required:false,
    started_at:typeof value?.started_at==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value.started_at)?value.started_at:null,
    hotel_identity_verification:'NOT_VERIFIED',statistics_date_verification:'NOT_VERIFIED',
    omitted_parameters:['hotel_identity','dataScope','runtime_verification_fields'],
    results,current_page_unchanged:value?.current_page_unchanged===true,
    focus_state_unchanged:value?.focus_state_unchanged===true,
    received_all_cards:results.length===3&&new Set(results.map(r=>r.route)).size===3&&results.every(r=>r.status==='data_received_unverified'&&r.cards.length===ROUTES[r.route]&&r.cards.every(c=>c.title&&c.value!==null&&!['-','--','—'].includes(c.value)))};
}

// Experimental session-only requests. Missing discovery parameters are NOT guessed.
// This function is serialized into the inspected page; it has no module dependencies.
export function directProbe(args, diagnose) {
  if(typeof diagnose!=='function')return 'diagnostics_unavailable';
  if (location.origin !== 'https://me.meituan.com' || location.href !== args.expectedUrl) return 'page_changed';
  let decoded = location.href;
  for (let i=0;i<3;i++) { try { decoded=decodeURIComponent(decoded); } catch { break; } }
  if (/data[-_]?center|\/login|passport|signin/i.test(decoded)) return 'ordinary_page_required';
  if (!/^__mt_probe_[a-f0-9]{32}$/.test(args.key)) return 'blocked';
  const routes = [
    ['/api/v1/ebooking/home/businessData', ['引流价','曝光量','浏览人数','支付转化率','支付订单数','销售间夜','销售均价','销售额','入住间夜','满房率'], true],
    ['/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/score', ['HOS分','信息分','评价分'], false],
    ['/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/projectEffect/customerAcquisition', ['获得获客币','曝光量','浏览量','花费获客币'], true]
  ];
  const before=location.href, focus=document.hasFocus(), controller=new AbortController();
  const report={version:'0.3.2',mode:'direct_session_probe',started_at:new Date().toISOString(),
    source:'fixed_paths_current_browser_session',capture_required:false,
    hotel_identity_verification:'NOT_VERIFIED',statistics_date_verification:'NOT_VERIFIED',
    omitted_parameters:['hotel_identity','dataScope','runtime_verification_fields'],results:[],overall:'NOT_VERIFIED'};
  const slot={done:false,result:null,cancel:()=>controller.abort()};
  window[args.key]=slot;
  const cleanup=setTimeout(()=>{controller.abort();delete window[args.key];},65000);
  cleanup?.unref?.();
  const safeNumber = v => {
    const s=typeof v==='number'?String(v):v;
    return typeof s==='string' && /^(?:—|-|--|[+-]?\d{1,7}(?:\.\d{1,4})?\s*(?:%|万|万元|万次|元|次|人|单|间夜|个|分|\/\s*\d{1,4})?)$/.test(s)?s:null;
  };
  const units=new Set(['元','次','人','单','间夜','%','万','万元','万次','个','分','']);
  void (async()=>{
    try {
      for (const [path,titles,yesterday] of routes) {
        if (controller.signal.aborted || location.href!==before) break;
        const result={route:path,status:'request_failed',http_status:null,business_status:null,cards:[]};
        const timeout=setTimeout(()=>controller.abort(),15000);
        try {
          const response=await fetch('https://eb.meituan.com'+path+(yesterday?'?dateRange=1':''),
            {method:'GET',credentials:'include',redirect:'manual',signal:controller.signal});
          result.http_status=response.status;
          if (response.type==='opaqueredirect' || response.status>=300&&response.status<400) result.status='redirect_blocked';
          else if (response.status===401) result.status='login_expired';
          else if (response.status===403||response.status===429) result.status='blocked';
          else if (response.ok) {
            const reader=response.body?.getReader();
            if (!reader) throw new Error('body_unavailable');
            let size=0;const chunks=[];
            while(true) {const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>1000000){await reader.cancel();throw new Error('oversize');}chunks.push(part.value);}
            const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}
            const text=new TextDecoder().decode(bytes);
            result.diagnostics=diagnose(text);
            const data=JSON.parse(text);
            result.business_status=Number.isSafeInteger(data?.status)&&Math.abs(data.status)<100000?data.status:null;
            const cards=data?.data?.cards;
            if(result.diagnostics.category==='merchant_authorization_failed') {
              result.status='merchant_authorization_failed';
              result.business_status=33202;
            }
            else if(typeof data?.status!=='number') result.status='response_contract_mismatch';
            else if(data?.status!==0) result.status='business_failed';
            else if(!Array.isArray(cards)||cards.length!==titles.length||cards.some((c,i)=>c?.title!==titles[i]))result.status='shape_mismatch';
            else {
              result.cards=cards.map((c,i)=>({title:titles[i],value:safeNumber(c.value),unit:units.has(c.unit)?c.unit:null,suffix:units.has(c.suffix)?c.suffix:null}));
              result.status=result.cards.every(c=>c.value!==null&&!['-','--','—'].includes(c.value))?'data_received_unverified':'data_incomplete';
            }
          }
        } catch {result.status=controller.signal.aborted?'timeout':'network_or_parse_failed';}
        finally {clearTimeout(timeout);}
        report.results.push(result);
        // Do not continue after authentication/security/network failure.
        if(!['data_received_unverified','data_incomplete'].includes(result.status))break;
      }
    } finally {
      report.current_page_unchanged=location.href===before;
      report.focus_state_unchanged=document.hasFocus()===focus;
      report.received_all_cards=report.results.length===3&&report.results.every(r=>r.status==='data_received_unverified');
      slot.result=report;slot.done=true;
      // Retain the bounded cleanup in case the panel is closed before polling.
    }
  })();
  return 'started';
}
