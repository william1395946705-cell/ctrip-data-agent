// Independent DevTools boundary: raw HAR objects/headers never cross into output.
const secret = /cookie|auth|token|session|csrf|password|secret|credential|ticket|signature|device|trace|account|user|phone|mobile|email|address|name|uuid|guid|(?:^|_)id$|Id$|ID$|partner|poi/i;
const metric = /^(?:[a-z_]*(?:exposure|browse|visitor|conversion|rate|price|amount|revenue|score|rank|roomnight|room_night|ordercount|order_count|ordernum|order_num|uv|pv)[a-z_]*|totalRecords|totalPages|count|code|rcode)$/i;
export function allowedUrl(raw) {
  try { const u = new URL(raw); return u.protocol === 'https:' && !u.username && !u.password &&
    (!u.port || u.port === '443') && ['meituan.com','dianping.com'].some(d => u.hostname === d || u.hostname.endsWith('.'+d)); } catch { return false; }
}
export function targetEndpoint(raw) {
  try {
    const u = new URL(raw);
    if (!allowedUrl(raw) || u.hostname !== 'eb.meituan.com') return false;
    return /^\/api\/(?:v\d{1,2}|\d{1,2})\/ebooking\/home\/businessData$/.test(u.pathname) ||
      ['/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/score',
       '/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/projectEffect/customerAcquisition'].includes(u.pathname);
  } catch { return false; }
}
const titles = new Set(['引流价','曝光量','浏览人数','支付转化率','支付订单数','销售间夜','销售均价','销售额','入住间夜','满房率','HOS分','信息分','评价分','获得获客币','获取获客币','浏览量','花费获客币']);
const units = new Set(['元','次','人','单','间夜','%','万','万元','万次','个','分','']);
function displayNumber(v) {
  const s = typeof v === 'number' ? String(v) : v;
  if (typeof s !== 'string') return null;
  if (/^(?:—|-|--)$/.test(s)) return s;
  // Bounded display quantities/ranks only, never arbitrary strings or IDs.
  return /^[+-]?\d{1,7}(?:\.\d{1,4})?\s*(?:%|万|万元|万次|元|次|人|单|间夜|个|分|\/\s*\d{1,4})?$/.test(s) ? s : null;
}
export function targetSamples(value) {
  const cards = value?.data?.cards;
  if (!Array.isArray(cards)) return {state:'cards_missing'};
  return {state:'candidate_only', card_count:cards.length, sampled_card_count:Math.min(cards.length,20),
    business_status:typeof value.status === 'number' && Number.isSafeInteger(value.status) && Math.abs(value.status)<100000 ? value.status : null,
    cards:cards.slice(0,20).map((card,index)=>{
      const title = titles.has(card?.title) ? card.title : null;
      if (!title) return {index,title:null,omitted:'unrecognized_title'};
      return {index,title,value:displayNumber(card.value), unit:units.has(card.unit)?card.unit:null,
        suffix:units.has(card.suffix)?card.suffix:null,
        extAttrs:Array.isArray(card.extAttrs)?card.extAttrs.slice(0,6).map((a,i)=>({index:i,
          values:(Array.isArray(a?.values)?a.values:[a?.values]).slice(0,4).map(displayNumber),
          unit:units.has(a?.unit)?a.unit:null})):[]};
    })};
}
function keyName(key) {
  if (secret.test(key)) return '[private-field]';
  return /^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/.test(key) ? key : '[dynamic-field]';
}
export function structure(value) {
  let remaining = 1500;
  function walk(v, depth, key = '') {
    if (--remaining < 0 || depth > 8) return {type:'truncated'};
    if (secret.test(key)) return {type:'redacted'};
    if (v === null) return {type:'null'};
    if (Array.isArray(v)) return {type:'array', length:v.length, items:v.slice(0,3).map(x=>walk(x,depth+1))};
    if (typeof v === 'object') return {type:'object', fields:Object.fromEntries(Object.entries(v).slice(0,100).map(([k,x])=>[keyName(k),walk(x,depth+1,k)]))};
    const result = {type:typeof v};
    if (metric.test(key) && typeof v === 'number' && Number.isFinite(v) && Math.abs(v) < 1e10) result.sample = v;
    if (/date|time|start|end/i.test(key) && typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)) result.sample = v;
    return result;
  }
  return walk(value,0);
}
export function safeRequest(entry) {
  const req = entry.request || {};
  if (!allowedUrl(req.url)) return null;
  const u = new URL(req.url);
  const target = targetEndpoint(req.url);
  const path = target ? u.pathname : u.pathname.split('/').map(p => /^[a-zA-Z_-]{0,64}$/.test(p) ? p : '[dynamic]').join('/');
  let payload = {type:'absent'};
  if (typeof req.postData?.text === 'string') {
    if (req.postData.text.length > 100000) payload = {type:'oversize'};
    else { try { payload = structure(JSON.parse(req.postData.text)); } catch { payload = {type:'non_json_omitted'}; } }
  }
  const mime = String(entry.response?.content?.mimeType || '').split(';')[0];
  return {request_url:u.origin+path, method:['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'].includes(req.method)?req.method:'OTHER',
    query_fields:[...new Set([...u.searchParams.keys()].map(keyName))].slice(0,100), payload_schema:payload,
    query_samples:target ? Object.fromEntries(['dateRange','dataScope'].filter(k=>u.searchParams.has(k)).map(k=>{
      const v=u.searchParams.get(k);
      return [k,/^(?:\d{1,2}|yesterday|YESTERDAY|today|TODAY|day|DAY|week|WEEK|month|MONTH|\d{4}-\d{2}-\d{2})$/.test(v)?v:'[value-omitted]'];
    })) : {},
    http_status:Number.isInteger(entry.response?.status)?entry.response.status:null,
    content_type:['application/json','text/json','text/html','text/plain'].includes(mime)?mime:'other',
    observed_at:new Date().toISOString(), result:'NOT VERIFIED'};
}
export function responseStructure(content, encoding, requestUrl = '') {
  if (encoding || typeof content !== 'string') return {type:'unavailable_or_encoded'};
  if (content.length > 1000000) return {type:'oversize'};
  try {
    const parsed=JSON.parse(content), result=structure(parsed);
    if (targetEndpoint(requestUrl)) result.business_samples=targetSamples(parsed);
    return result;
  } catch { return {type:'non_json_omitted'}; }
}
