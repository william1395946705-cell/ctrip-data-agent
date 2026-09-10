// Exact routes observed in the authorized discovery export. Never infer a route.
export const ROUTES = Object.freeze({
  '/api/v1/ebooking/home/businessData': 10,
  '/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/score': 3,
  '/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/projectEffect/customerAcquisition': 4
});
export function exactRoute(raw) {
  try { const u=new URL(raw); return u.origin==='https://eb.meituan.com' && !u.username && !u.password && !u.hash && Object.hasOwn(ROUTES,u.pathname) ? u.pathname : null; } catch { return null; }
}
export function ordinaryPage(raw) {
  try {
    const u=new URL(raw);
    let decoded=u.href;
    for(let i=0;i<2;i++) { try { decoded=decodeURIComponent(decoded); } catch { break; } }
    return u.origin==='https://me.meituan.com' && !/data[-_]?center|\/login|passport|signin/i.test(decoded);
  } catch { return false; }
}
export function dataCenterPage(raw) {
  try {
    const u = new URL(raw);
    let decoded = u.href;
    for (let i = 0; i < 3; i++) { try { decoded = decodeURIComponent(decoded); } catch { break; } }
    return u.origin === 'https://me.meituan.com' && /\/ebooking\/data-center\/index\.html(?:$|[?#])/i.test(decoded);
  } catch { return false; }
}
export function prepareTemplate(entry, samples, now=Date.now()) {
  const route=exactRoute(entry?.request?.url);
  if (!route || entry.request.method!=='GET' || entry.response?.status!==200 || samples?.business_status!==0) return null;
  if (!Array.isArray(samples.cards) || samples.cards.length!==ROUTES[route] || samples.cards.some(c=>!c.title || c.value===null)) return null;
  const u=new URL(entry.request.url);
  if (route!=='/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/score' && u.searchParams.get('dateRange')!=='1') return null;
  const identity=[...u.searchParams].filter(([k])=>/^(?:poi|hotel|partner|bizAccount|merchant)(?:Id|ID|_id)?s?$/i.test(k));
  if (!identity.length || identity.some(([,v])=>!v)) return null;
  return {route,url:entry.request.url,captured:now,identity,
    baseline:samples.cards.map(c=>({title:c.title,value:c.value,suffix:c.suffix,unit:c.unit}))};
}
export function validateSet(templates, now=Date.now()) {
  if (templates.length!==3 || new Set(templates.map(t=>t.route)).size!==3) return 'capture_three_endpoints_first';
  if (templates.some(t=>!exactRoute(t.url) || exactRoute(t.url)!==t.route || now<t.captured || now-t.captured>600000)) return 'capture_expired';
  if (templates.some(t=>!Array.isArray(t.baseline) || t.baseline.length!==ROUTES[t.route] ||
    t.baseline.some((card,i)=>!card || typeof card.title!=='string' || card.title!==t.baseline[i].title || card.value===null))) return 'capture_invalid_baseline';
  const day=t=>new Date(t+8*3600000).toISOString().slice(0,10);
  if (templates.some(t=>day(t.captured)!==day(now))) return 'capture_date_changed';
  const common=templates[0].identity.filter(([key,value])=>templates.every(t=>t.identity.some(([k,v])=>k===key && v===value)));
  if (!common.length) return 'capture_identity_unbound';
  const ids=new Map();
  for (const t of templates) for(const [key,value] of t.identity) {
    if(ids.has(key) && ids.get(key)!==value) return 'capture_identity_conflict';
    ids.set(key,value);
  }
  return null;
}

// Serialized into the inspected TOP page, not the extension origin or a hidden frame.
// Only sanitized scalar projections are exposed to the DevTools panel.
export function pageRequest(args) {
  if (location.href!==args.expectedUrl || location.origin!=='https://me.meituan.com') return 'page_changed';
  const paths=[
    '/api/v1/ebooking/home/businessData',
    '/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/score',
    '/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/projectEffect/customerAcquisition'
  ];
  let u; try {u=new URL(args.url);} catch {return 'blocked';}
  if(u.origin!=='https://eb.meituan.com' || u.username || u.password || u.hash || !paths.includes(u.pathname)) return 'blocked';
  if (!/^__mt_probe_[a-f0-9]{32}$/.test(args.key)) return 'blocked';
  const before=location.href, focus=document.hasFocus(), controller=new AbortController();
  const slot={done:false,result:null,cancel:()=>controller.abort()};
  window[args.key]=slot;
  const timeout=setTimeout(()=>controller.abort(),15000);
  // Bounded cleanup even if DevTools is closed during execution.
  const cleanup=setTimeout(()=>{controller.abort();delete window[args.key];},30000);
  cleanup?.unref?.();
  void (async()=>{
    let result={status:'request_failed',http_status:null};
    try {
      const response=await fetch(args.url,{method:'GET',credentials:'include',redirect:'manual',signal:controller.signal});
      result.http_status=response.status;
      if(response.type==='opaqueredirect' || response.status>=300 && response.status<400) result.status='redirect_blocked';
      else if(response.status===401) result.status='login_expired';
      else if(response.status===403 || response.status===429) result.status='blocked';
      else if(response.ok) {
        const reader=response.body?.getReader();
        if (!reader) throw new Error('missing_body');
        let size=0;const chunks=[];
        while(true) { const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>1000000){await reader.cancel();throw new Error('oversize');}chunks.push(part.value); }
        const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}
        const data=JSON.parse(new TextDecoder().decode(bytes));
        result.business_status=Number.isSafeInteger(data?.status)&&Math.abs(data.status)<100000?data.status:null;
        const cards=data?.data?.cards;
        if (data?.status!==0) result.status='business_failed';
        else if(!Array.isArray(cards) || cards.length!==args.baseline.length || cards.some((c,i)=>c?.title!==args.baseline[i].title)) result.status='shape_mismatch';
        else {
          const safeNumber=v=>typeof v==='string' && /^(?:—|-|--|[+-]?\d{1,7}(?:\.\d{1,4})?\s*(?:%|万|万元|万次|元|次|人|单|间夜|个|分|\/\s*\d{1,4})?)$/.test(v)?v:null;
          result.card_count=cards.length;
          result.comparisons=cards.map((c,i)=>{
            const b=args.baseline[i], v=safeNumber(c.value);
            return {title:b.title,old:b.value,replay:v,equal:v!==null&&v===b.value&& (c.suffix??null)===b.suffix && (c.unit??null)===b.unit};
          });
          result.status=result.comparisons.every(c=>c.equal)?'match':'mismatch_or_time_drift';
        }
      }
    } catch {
      result.status=controller.signal.aborted?'timeout':'network_or_parse_failed';
    } finally {
      clearTimeout(timeout);
      clearTimeout(cleanup);
      result.current_page_unchanged=location.href===before;
      result.focus_unchanged=document.hasFocus()===focus;
      if(!result.current_page_unchanged)result.status='page_changed';
      slot.result=result;slot.done=true;
    }
  })();
  return 'started';
}
