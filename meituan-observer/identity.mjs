// Serialized into each existing allowed frame. Return only business identity
// candidates; full resource URLs and authentication parameters never leave it.
export function observeIdentity() {
  const origins=['https://me.meituan.com','https://eb.meituan.com'];
  if(!origins.includes(location.origin))return {state:'unsupported'};
  const pairs=new Map(); let malformed=false;
  const now=performance.now();
  for(const e of performance.getEntriesByType('resource')) {
    if(!Number.isFinite(e.startTime)||now-e.startTime>600000||e.startTime>now)continue;
    try {
      const u=new URL(e.name);
      if(!origins.includes(u.origin)||u.username||u.password)continue;
      const p=u.searchParams.getAll('poiId'), a=u.searchParams.getAll('partnerId');
      if(!p.length||!a.length)continue;
      if(p.length!==1||a.length!==1||![p[0],a[0]].every(v=>/^[1-9]\d{0,17}$/.test(v))) {malformed=true;continue;}
      pairs.set(p[0]+':'+a[0],{poiId:p[0],partnerId:a[0]});
    } catch {}
  }
  if(malformed||pairs.size>1)return {state:'conflict'};
  return pairs.size?{state:'candidate',identity:[...pairs.values()][0]}:{state:'missing'};
}

export function resolveIdentity(observations) {
  const pairs=new Map();
  for(const o of observations) {
    if(o?.state==='conflict')return {state:'conflict'};
    if(o?.state!=='candidate')continue;
    const x=o.identity;
    if(!x||![x.poiId,x.partnerId].every(v=>typeof v==='string'&&/^[1-9]\d{0,17}$/.test(v)))return {state:'conflict'};
    pairs.set(x.poiId+':'+x.partnerId,{poiId:x.poiId,partnerId:x.partnerId});
  }
  if(pairs.size>1)return {state:'conflict'};
  return pairs.size?{state:'candidate',identity:[...pairs.values()][0]}:{state:'missing'};
}
