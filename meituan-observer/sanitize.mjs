// Independent DevTools boundary: raw HAR objects/headers never cross into output.
const secret = /cookie|auth|token|session|csrf|password|secret|credential|ticket|signature|device|trace|account|user|phone|mobile|email|address|name|uuid|guid|(?:^|_)id$|Id$|ID$|partner|poi/i;
const metric = /^(?:[a-z_]*(?:exposure|browse|visitor|conversion|rate|price|amount|revenue|score|rank|roomnight|room_night|ordercount|order_count|ordernum|order_num|uv|pv)[a-z_]*|totalRecords|totalPages|count|code|rcode)$/i;
export function allowedUrl(raw) {
  try { const u = new URL(raw); return u.protocol === 'https:' && !u.username && !u.password &&
    (!u.port || u.port === '443') && ['meituan.com','dianping.com'].some(d => u.hostname === d || u.hostname.endsWith('.'+d)); } catch { return false; }
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
  const path = u.pathname.split('/').map(p => /^[a-zA-Z_-]{0,64}$/.test(p) ? p : '[dynamic]').join('/');
  let payload = {type:'absent'};
  if (typeof req.postData?.text === 'string') {
    if (req.postData.text.length > 100000) payload = {type:'oversize'};
    else { try { payload = structure(JSON.parse(req.postData.text)); } catch { payload = {type:'non_json_omitted'}; } }
  }
  const mime = String(entry.response?.content?.mimeType || '').split(';')[0];
  return {request_url:u.origin+path, method:['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'].includes(req.method)?req.method:'OTHER',
    query_fields:[...new Set([...u.searchParams.keys()].map(keyName))].slice(0,100), payload_schema:payload,
    http_status:Number.isInteger(entry.response?.status)?entry.response.status:null,
    content_type:['application/json','text/json','text/html','text/plain'].includes(mime)?mime:'other',
    observed_at:new Date().toISOString(), result:'NOT VERIFIED'};
}
export function responseStructure(content, encoding) {
  if (encoding || typeof content !== 'string') return {type:'unavailable_or_encoded'};
  if (content.length > 1000000) return {type:'oversize'};
  try { return structure(JSON.parse(content)); } catch { return {type:'non_json_omitted'}; }
}
