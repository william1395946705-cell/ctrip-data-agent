import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {directProbe,safeDirectReport} from './direct.mjs';
const titles=[['引流价','曝光量','浏览人数','支付转化率','支付订单数','销售间夜','销售均价','销售额','入住间夜','满房率'],['HOS分','信息分','评价分'],['获得获客币','曝光量','浏览量','花费获客币']];
async function run({http=200,status=0,html=false,href='https://me.meituan.com/home',change=false,dash=false,network=false}={}) {
  const calls=[];const timers=[];const key='__mt_probe_0123456789abcdef0123456789abcdef';
  const context={location:{href,origin:new URL(href).origin},document:{hasFocus:()=>false},window:{},AbortController,TextDecoder,Uint8Array,URL,
    setTimeout:fn=>{timers.push(fn);return 1;},clearTimeout:()=>{},
    fetch:async(url,opts)=>{
      calls.push({url,opts});if(network)throw new Error('SECRET');
      if(change)context.location.href='https://me.meituan.com/other';
      const cards=titles[calls.length-1].map(title=>({title,value:dash?'-':'2',unit:'',token:'SECRET'}));
      return new Response(html?'<html>SECRET</html>':JSON.stringify({status,data:{cards},token:'SECRET'}),{status:http});
    }};
  vm.createContext(context);
  const started=vm.runInContext(`(${directProbe.toString()})(${JSON.stringify({expectedUrl:href,key})})`,context);
  for(let i=0;i<100&&!context.window[key]?.done&&started==='started';i++)await new Promise(r=>setImmediate(r));
  return {started,calls,report:context.window[key]?.result,timers,context,key};
}
test('cold ordinary page sends three fixed GETs without captured URLs and never claims verified',async()=>{
  const x=await run();assert.equal(x.started,'started');assert.equal(x.calls.length,3);
  for(const c of x.calls){assert.equal(c.opts.method,'GET');assert.equal(c.opts.credentials,'include');assert.equal(c.opts.redirect,'manual');assert.ok(!c.opts.headers);assert.deepEqual([...new URL(c.url).searchParams.keys()],c.url.includes('/score')?[]:['dateRange']);}
  const report=safeDirectReport(x.report);assert.equal(report.received_all_cards,true);assert.equal(report.overall,'NOT_VERIFIED');assert.equal(report.capture_required,false);assert.equal(JSON.stringify(report).includes('SECRET'),false);
  x.timers.at(0)();assert.equal(x.context.window[x.key],undefined);
});
test('data center and login pages send no direct requests',async()=>{
  for(const href of ['https://me.meituan.com/login','https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fdata-center%2Findex.html','https://example.test/']){const x=await run({href});assert.notEqual(x.started,'started');assert.equal(x.calls.length,0);}
});
test('auth, business, HTML and network failures stop the round and never become no data',async()=>{
  for(const [options,status] of [[{http:401},'login_expired'],[{http:403},'blocked'],[{http:429},'blocked'],[{status:5},'business_failed'],[{html:true},'network_or_parse_failed'],[{network:true},'network_or_parse_failed']]){
    const x=await run(options);assert.equal(x.calls.length,1);assert.equal(x.report.results[0].status,status);assert.equal(x.report.overall,'NOT_VERIFIED');
  }
});
test('navigation during first response prevents remaining requests',async()=>{
  const x=await run({change:true});assert.equal(x.calls.length,1);assert.equal(x.report.current_page_unchanged,false);
});
test('dash values are incomplete, not zero or successful data',async()=>{
  const x=await run({dash:true});assert.equal(x.report.received_all_cards,false);assert.equal(x.report.results[0].status,'data_incomplete');
});
test('export reconstructs page-supplied values without arbitrary fields or messages',()=>{
  const x=safeDirectReport({token:'SECRET',overall:'PASS',results:[{route:'/api/v1/ebooking/home/businessData',status:'SECRET',message:'SECRET',cards:[{title:'曝光量',value:'SECRET',token:'SECRET'}]}]});
  assert.equal(JSON.stringify(x).includes('SECRET'),false);assert.equal(x.overall,'NOT_VERIFIED');assert.equal(x.received_all_cards,false);
});
