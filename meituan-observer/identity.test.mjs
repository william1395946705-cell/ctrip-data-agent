import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {observeIdentity,resolveIdentity} from './identity.mjs';
function observe(urls,origin='https://me.meituan.com') {
  return vm.runInNewContext(`(${observeIdentity.toString()})()`,{location:{origin},URL,
    performance:{now:()=>700000,getEntriesByType:()=>urls.map(x=>typeof x==='string'?{name:x,startTime:699000}:x)}});
}
test('only paired numeric candidates from exact origins survive; credentials do not',()=>{
  const x=observe(['https://eb.meituan.com/ordinary?poiId=123&partnerId=456&token=SECRET']);
  assert.equal(x.state,'candidate');assert.equal(x.identity.poiId,'123');
  assert.equal(JSON.stringify(x).includes('SECRET'),false);
  assert.equal(JSON.stringify(x).includes('/ordinary'),false);
  for(const url of ['https://eb.meituan.com.evil.test/?poiId=123&partnerId=456','https://eb.meituan.com/?poiId=123','https://eb.meituan.com/?partnerId=456'])assert.equal(observe([url]).state,'missing');
});
test('stale entries, conflicting pairs, duplicate keys and cross-frame conflicts fail closed',()=>{
  const u='https://eb.meituan.com/a?poiId=123&partnerId=456';
  assert.equal(observe([{name:u,startTime:1}]).state,'missing');
  assert.equal(observe([u,u+'&poiId=123']).state,'conflict');
  assert.equal(observe([u,u.replace('123','789')]).state,'conflict');
  assert.equal(resolveIdentity([observe([u]),observe([u.replace('123','789')])]).state,'conflict');
  assert.equal(resolveIdentity([observe([u]),observe([u])]).state,'candidate');
  assert.equal(observe([u],'https://evil.test').state,'unsupported');
});
