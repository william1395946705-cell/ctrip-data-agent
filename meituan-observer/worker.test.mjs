import test from 'node:test';
import assert from 'node:assert/strict';
let listener, stored={}, mode='candidate', polls=0, calls=[];
const identity={poiId:'123456',partnerId:'789012'};
globalThis.chrome={
  tabs:{query:async()=>[{id:1,url:'https://me.meituan.com/home'}],onRemoved:{addListener(){}}},
  storage:{local:{get:async()=>stored,set:async x=>{stored=x;}}},
  runtime:{id:'test',getURL:p=>'chrome-extension://test/'+p,onMessage:{addListener:fn=>listener=fn}},
  scripting:{executeScript:async x=>{
    calls.push(x);
    if(x.func.name==='observeIdentity')return [{frameId:0,documentId:'doc',result:mode==='candidate'?{state:mode,identity}:{state:mode}}];
    if(x.func.name==='directProbe')return [{result:'started'}];
    polls++;return [{result:{started_at:'2026-09-10T00:00:00.000Z',results:[{route:'/api/v1/ebooking/home/businessData',status:'merchant_authorization_failed',http_status:200,business_status:33202,cards:[]}],current_page_unchanged:true,focus_state_unchanged:true,token:'SECRET'}}];
  }}
};
await import('./worker.mjs');
const sender={id:'test',url:'chrome-extension://test/popup.html'};
const request=action=>new Promise(resolve=>listener({action},sender,resolve));
test('popup bridge blocks missing/conflicting IDs without requests and retains only safe result',async()=>{
  for(const s of ['missing','conflict']){mode=s;const r=await request('collect');assert.equal(r.ok,false);assert.equal(r.error,'identity_'+s);}
  assert.equal(calls.some(c=>c.func.name==='directProbe'),false);
  mode='candidate';const s=await request('status');assert.equal(JSON.stringify(s).includes('123456'),false);
  const r=await request('collect');assert.equal(r.ok,true);assert.equal(r.value.received_all_cards,false);
  assert.equal(polls,1);assert.equal(JSON.stringify(stored).includes('SECRET'),false);assert.equal(JSON.stringify(stored).includes('123456'),false);
  assert.equal((await request('collect')).error,'cooldown');
  assert.equal(listener({action:'collect'},{id:'test',url:'https://evil.test'},()=>assert.fail()),undefined);
});
