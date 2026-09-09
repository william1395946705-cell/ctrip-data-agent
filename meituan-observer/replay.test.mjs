import test from 'node:test';
import assert from 'node:assert/strict';
import {ROUTES, dataCenterPage, ordinaryPage, exactRoute, prepareTemplate, validateSet, pageRequest} from './replay.mjs';

const routes = Object.keys(ROUTES);
const cards = route => Array.from({length: ROUTES[route]}, (_, i) => ({
  title: ['曝光量','浏览人数','支付转化率','支付订单数','销售间夜','销售均价','销售额','入住间夜','满房率','引流价','HOS分','信息分','评价分','获得获客币'][i] || `指标${i}`,
  value: String(i + 1), suffix: '', unit: ''
}));
const entry = (route, identity='777') => ({request:{url:`https://eb.meituan.com${route}?dateRange=1&hotelId=${identity}`, method:'GET'}, response:{status:200}});
const sample = route => ({business_status:0, cards:cards(route)});

test('page gates distinguish data-center capture from ordinary page replay', () => {
  assert.equal(dataCenterPage('https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fdata-center%2Findex.html'), true);
  assert.equal(dataCenterPage('https://me.meituan.com/ebooking/home/mainland'), false);
  assert.equal(ordinaryPage('https://me.meituan.com/ebooking/home/mainland'), true);
  assert.equal(ordinaryPage('https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febooking%2Fdata-center%2Findex.html'), false);
  assert.equal(ordinaryPage('https://evil.test/ebooking/home'), false);
});

test('templates require exact observed routes, yesterday range, identity, and complete cards', () => {
  const now = Date.parse('2026-09-09T01:00:00Z');
  const templates = routes.map(route => prepareTemplate(entry(route), sample(route), now));
  assert.equal(exactRoute(templates[0].url), routes[0]);
  assert.equal(validateSet(templates, now), null);
  const score = prepareTemplate({...entry(routes[1]), request:{...entry(routes[1]).request, url:`https://eb.meituan.com${routes[1]}?hotelId=777`}}, sample(routes[1]), now);
  assert.ok(score);
  const missing = prepareTemplate({request:{url:`https://eb.meituan.com${routes[0]}?dateRange=1`,method:'GET'},response:{status:200}}, sample(routes[0]), now);
  assert.equal(missing, null);
  assert.equal(validateSet(templates.map(t => ({...t, route:routes[1]})), now), 'capture_three_endpoints_first');
});

test('templates expire and conflicting identities fail closed', () => {
  const now = Date.parse('2026-09-09T01:00:00Z');
  const templates = routes.map(route => prepareTemplate(entry(route), sample(route), now));
  assert.equal(validateSet(templates, now + 600001), 'capture_expired');
  const conflict = templates.map((t, i) => ({...t, identity:[['hotelId','777'], ['poiId', i ? 'different' : 'same']]}));
  assert.equal(validateSet(conflict, now), 'capture_identity_conflict');
});

test('pageRequest uses the inspected page session and returns only safe comparison data', async () => {
  const route = routes[0];
  const key = '__mt_probe_0123456789abcdef0123456789abcdef';
  const baseline = cards(route);
  globalThis.location = {href:'https://me.meituan.com/ebooking/home/mainland', origin:'https://me.meituan.com'};
  globalThis.document = {hasFocus: () => true};
  globalThis.window = {};
  const body = JSON.stringify({status:0,data:{cards:baseline}});
  let reads = 0;
  globalThis.fetch = async () => ({status:200, ok:true, type:'basic', body:{getReader:()=>({read:async()=>reads++ ? {done:true} : {done:false,value:new TextEncoder().encode(body)}, cancel:async()=>{}})}});
  assert.equal(pageRequest({expectedUrl:location.href,url:`https://eb.meituan.com${route}?dateRange=1&hotelId=777`,key,baseline}), 'started');
  for (let i=0; i<30 && !window[key]?.done; i++) await new Promise(resolve => setTimeout(resolve, 1));
  assert.equal(window[key].result.status, 'match');
  assert.equal(window[key].result.http_status, 200);
  assert.equal(window[key].result.current_page_unchanged, true);
  assert.equal(window[key].result.comparisons.length, 10);
});
