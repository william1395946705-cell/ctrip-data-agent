import test from 'node:test';
import assert from 'node:assert/strict';
import {safeRequest,structure,responseStructure,allowedUrl} from './sanitize.mjs';
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
