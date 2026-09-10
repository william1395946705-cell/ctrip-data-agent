// Serialize this pure function alongside the page probe. No arbitrary message,
// field name, identifier, URL, or authentication value is returned.
export function responseDiagnostics(input) {
  let value=input, format='json';
  if(typeof input==='string') {
    try {value=JSON.parse(input);} catch {
      return {format:/^\s*</.test(input)?'html_or_markup':'non_json',fields:[],codes:[],category:'unclassified',mentioned_parameters:[]};
    }
  }
  const known=['status','code','errorCode','errno','success','data','error','errors','result','message','msg','errorMsg','errorMessage'];
  const params=['hotelId','poiId','poiid','partnerId','bizAccountId','merchantId','dateRange','dataScope','yodaReady','csecplatform','csecversion'];
  const fields=[],codes=[],messages=[];
  const type=v=>v===null?'null':Array.isArray(v)?'array':typeof v;
  let remaining=80;
  function walk(v,path,depth) {
    if(!v || typeof v!=='object' || Array.isArray(v) || depth>2 || remaining--<=0)return;
    for(const k of known)if(Object.hasOwn(v,k)) {
      const p=path?path+'.'+k:k;
      fields.push({path:p,type:type(v[k])});
      if(['status','code','errorCode','errno'].includes(k)) {
        const n=typeof v[k]==='number'?v[k]:typeof v[k]==='string'&&/^-?\d{1,5}$/.test(v[k])?Number(v[k]):null;
        codes.push({path:p,type:type(v[k]),value:Number.isSafeInteger(n)&&Math.abs(n)<100000?n:null});
      }
      if(['message','msg','errorMsg','errorMessage','error'].includes(k)&&typeof v[k]==='string')messages.push(v[k].slice(0,4096));
      if(['data','error','errors','result'].includes(k))walk(v[k],p,depth+1);
    }
  }
  walk(value,'',0);
  const message=messages.join(' ');
  // Heuristic categories, never treated as proof of an exact root cause.
  let category='unclassified';
  if(value?.code===33202 && messages.some(m=>m.trim()==='商家账号水平鉴权失败'))category='merchant_authorization_failed';
  else if(/验证码|安全验证|人机|captcha|risk|verify|verification|signature|签名|风控/i.test(message))category='security_validation_hint';
  else if(/登录|login|session|unauthenticated|authentication/i.test(message))category='authentication_hint';
  else if(/权限|鉴权|permission|forbidden|access denied/i.test(message))category='permission_hint';
  else if(/参数|parameter|argument|required|missing|不能为空|必填/i.test(message))category='parameter_hint';
  return {format:value===null?'json_null':Array.isArray(value)?'json_array':typeof value==='object'?format:'json_scalar',
    fields:fields.slice(0,80),codes:codes.slice(0,20),category,
    mentioned_parameters:params.filter(p=>new RegExp('(?:^|[^a-zA-Z0-9_])'+p+'(?:$|[^a-zA-Z0-9_])','i').test(message))};
}

export function safeDiagnostics(value) {
  const path=/^(?:(?:data|error|errors|result)\.){0,2}(?:status|code|errorCode|errno|success|data|error|errors|result|message|msg|errorMsg|errorMessage)$/;
  const types=new Set(['null','array','object','string','number','boolean','undefined']);
  return {
    format:['json','json_null','json_array','json_scalar','html_or_markup','non_json'].includes(value?.format)?value.format:'unknown',
    fields:(Array.isArray(value?.fields)?value.fields:[]).slice(0,80).filter(f=>f&&path.test(f.path)&&types.has(f.type)).map(f=>({path:f.path,type:f.type})),
    codes:(Array.isArray(value?.codes)?value.codes:[]).slice(0,20).filter(f=>f&&path.test(f.path)&&types.has(f.type)).map(f=>({path:f.path,type:f.type,value:Number.isSafeInteger(f.value)&&Math.abs(f.value)<100000?f.value:null})),
    category:['merchant_authorization_failed','security_validation_hint','authentication_hint','permission_hint','parameter_hint','unclassified'].includes(value?.category)?value.category:'unclassified',
    mentioned_parameters:(Array.isArray(value?.mentioned_parameters)?value.mentioned_parameters:[]).filter(p=>['hotelId','poiId','poiid','partnerId','bizAccountId','merchantId','dateRange','dataScope','yodaReady','csecplatform','csecversion'].includes(p)).slice(0,11)
  };
}

// Homepage natural-request metadata only. No body/headers or query values are read.
export function parameterPresence(entry) {
  try {
    const u=new URL(entry?.request?.url);
    if(!['https://me.meituan.com','https://eb.meituan.com'].includes(u.origin)||u.username||u.password)return null;
    const known=['hotelId','poiId','poiid','partnerId','bizAccountId','merchantId','dateRange','dataScope','yodaReady','csecplatform','csecversion'];
    const keys=[...u.searchParams.keys()];
    const routes=['/api/v1/ebooking/home/businessData','/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/score','/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/projectEffect/customerAcquisition'];
    return {origin:u.origin,route:u.origin==='https://eb.meituan.com'&&routes.includes(u.pathname)?u.pathname:null,method:['GET','POST'].includes(entry.request.method)?entry.request.method:'other',
      present:known.filter(k=>keys.includes(k)),other_parameter_count:Math.min(100,keys.filter(k=>!known.includes(k)).length)};
  } catch {return null;}
}
