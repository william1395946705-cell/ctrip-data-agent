import test from 'node:test';
import assert from 'node:assert/strict';
import {responseDiagnostics,safeDiagnostics,parameterPresence} from './diagnostics.mjs';
test('code alone is not proof of merchant authorization denial',()=>{
  assert.equal(responseDiagnostics({code:33202,error:{msg:'other'}}).category,'unclassified');
  assert.equal(responseDiagnostics({code:400,error:{msg:'鉴权失败'}}).category,'permission_hint');
});
test('ordinary metadata identifies only exact reviewed target routes',()=>{
  const route='/api/v1/ebooking/home/businessData';
  assert.equal(parameterPresence({request:{url:'https://eb.meituan.com'+route+'?poiId=SECRET',method:'GET'}}).route,route);
  for(const url of ['https://me.meituan.com'+route,'https://eb.meituan.com/private/SECRET'])assert.equal(parameterPresence({request:{url,method:'GET'}}).route,null);
});

test('missing status distinguishes alternate numeric envelope without exporting message',()=>{
  const x=responseDiagnostics(JSON.stringify({code:'400',message:'Missing required poiId: PRIVATE',data:null,token:'SECRET'}));
  assert.equal(x.category,'parameter_hint');
  assert.deepEqual(x.codes,[{path:'code',type:'string',value:400}]);
  assert.deepEqual(x.mentioned_parameters,['poiId','poiid']);
  assert.equal(JSON.stringify(x).includes('PRIVATE'),false);
  assert.equal(JSON.stringify(x).includes('SECRET'),false);
});
test('security and login hints stay hints and no symbolic code/message is exported',()=>{
  for(const [message,category] of [['安全验证 signature token SECRET','security_validation_hint'],['请重新登录 session SECRET','authentication_hint'],['无权限访问 SECRET','permission_hint']]) {
    const x=responseDiagnostics({error:{code:'SECRET',message}});
    assert.equal(x.category,category);assert.equal(x.codes[0].value,null);
    assert.equal(JSON.stringify(x).includes('SECRET'),false);
  }
});
test('HTML, null, arrays and string status are represented without treating them as success',()=>{
  assert.equal(responseDiagnostics('<html>SECRET</html>').format,'html_or_markup');
  assert.equal(responseDiagnostics('null').format,'json_null');
  assert.equal(responseDiagnostics('[]').format,'json_array');
  assert.deepEqual(responseDiagnostics({status:'0'}).codes,[{path:'status',type:'string',value:0}]);
});
test('unknown keys, deeply nested secrets and forged diagnostics cannot enter output',()=>{
  const x=responseDiagnostics({PRIVATE:{SECRET:'SECRET'},data:{error:{error:{code:'SECRET'}}}});
  assert.equal(JSON.stringify(x).includes('SECRET'),false);
  const y=safeDiagnostics({category:'SECRET',fields:[{path:'SECRET',type:'string'}],codes:[{path:'status',type:'string',value:'SECRET'}],mentioned_parameters:['SECRET'],message:'SECRET'});
  assert.equal(JSON.stringify(y).includes('SECRET'),false);
});
test('ordinary metadata keeps known key names only, not query values, headers, body, or path',()=>{
  const x=parameterPresence({request:{url:'https://me.meituan.com/PRIVATE?hotelId=SECRET&yodaReady=SECRET&auth=SECRET',method:'GET',headers:[{name:'Cookie',value:'SECRET'}],postData:{text:'SECRET'}}});
  assert.deepEqual(x.present,['hotelId','yodaReady']);
  assert.equal(JSON.stringify(x).includes('SECRET'),false);assert.equal(JSON.stringify(x).includes('PRIVATE'),false);
  assert.equal(parameterPresence({request:{url:'https://me.meituan.com.evil.test/x'}}),null);
});
