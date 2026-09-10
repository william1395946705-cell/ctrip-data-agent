import test from 'node:test';
import assert from 'node:assert/strict';
import {safeRequest,structure,responseStructure,allowedUrl,targetSamples} from './sanitize.mjs';
test('target capture retains reviewed parameter names but never identity or credential values',()=>{
  const result=safeRequest({request:{url:'https://eb.meituan.com/api/v1/ebooking/home/businessData?poiId=PRIVATE&partnerId=PRIVATE&dateRange=1&dataScope=PRIVATE&token=SECRET',method:'GET'}});
  assert.deepEqual(result.target_parameter_presence.present,['poiId','partnerId','dateRange','dataScope']);
  assert.equal(result.target_parameter_presence.route,'/api/v1/ebooking/home/businessData');
  for(const value of ['PRIVATE','SECRET']) assert.equal(JSON.stringify(result).includes(value),false);
  assert.equal(safeRequest({request:{url:'https://me.meituan.com/other?poiId=PRIVATE',method:'GET'}}).target_parameter_presence,null);
});
test('reject unrelated origins and credentials',()=>{
  for(const u of ['https://meituan.com.evil.test/a','http://me.meituan.com/a','https://u:p@me.meituan.com/a']) assert.equal(allowedUrl(u),false);
  assert.equal(allowedUrl('https://api.meituan.com/a'),true);
});
test('raw secrets and identities never exported; useful numeric samples survive',()=>{
  const entry = {request:{url:'https://me.meituan.com/api/123456?token=SECRET&date=2026-09-08',method:'POST',headers:[{name:'Cookie',value:'SECRET'}],postData:{text:JSON.stringify({token:'SECRET',hotelId:123456,beginDate:'2026-09-08',nested:{userName:'PRIVATE'}})}},response:{status:200,content:{mimeType:'application/json'}}};
  const result = JSON.stringify({req:safeRequest(entry),res:structure({exposure:2835,visitorCount:108,hotelId:123456,token:'SECRET',message:'PRIVATE',orderId:987654})});
  for(const s of ['SECRET','PRIVATE','123456','987654']) assert.equal(result.includes(s),false);
  assert.match(result,/2835/); assert.match(result,/2026-09-08/);
});
test('unknown strings, HTML, encoded content and oversized data fail closed',()=>{
  assert.equal(JSON.stringify(structure({value:'SECRET',arbitrary:'2835'})).includes('SECRET'),false);
  assert.equal(responseStructure('<html>SECRET</html>').type,'non_json_omitted');
  assert.equal(responseStructure('SECRET','base64').type,'unavailable_or_encoded');
  assert.equal(responseStructure('x'.repeat(1000001)).type,'oversize');
});
test('candidate cards retain all ten quantities but never arbitrary strings or identity',()=>{
  const data={status:0,data:{hotelId:123456789,cards:Array.from({length:10},()=>({title:'曝光量',value:'2835',unit:'次',token:'SECRET',extAttrs:[{values:['4/14','2758.79','SECRET','https://private.test']}]}))}};
  const result=targetSamples(data);
  assert.equal(result.cards.length,10);assert.equal(result.cards[9].value,'2835');
  assert.deepEqual(result.cards[0].extAttrs[0].values,['4/14','2758.79',null,null]);
  assert.equal(JSON.stringify(result).includes('SECRET'),false);
  assert.equal(JSON.stringify(result).includes('123456789'),false);
  assert.equal(targetSamples({data:{cards:[{title:'PRIVATE',value:'12345'}]}}).cards[0].omitted,'unrecognized_title');
});
test('display extraction is limited to three candidate routes; query credentials never retained',()=>{
  const url='https://eb.meituan.com/api/v1/ebooking/home/businessData?dateRange=1&dataScope=2&mtgsig=SECRET';
  const r=safeRequest({request:{url,method:'GET'}});
  assert.equal(r.request_url,'https://eb.meituan.com/api/v1/ebooking/home/businessData');
  assert.deepEqual(r.query_samples,{dateRange:'1',dataScope:'2'});
  const content=JSON.stringify({data:{cards:[{title:'曝光量',value:'2835'}]}});
  assert.equal(responseStructure(content,null,url).business_samples.cards[0].value,'2835');
  assert.equal(responseStructure(content,null,'https://eb.meituan.com/other').business_samples,undefined);
});
