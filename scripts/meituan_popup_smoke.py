"""Isolated Chromium integration test. All HTTP traffic is intercepted locally.
Never attaches to a user's browser, account, or profile.
"""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from playwright.async_api import async_playwright, expect


async def main():
    extension = Path(__file__).resolve().parents[1] / 'meituan-observer'
    requests = []
    async with async_playwright() as p:
        with TemporaryDirectory(prefix='meituan-popup-fixture-') as profile:
            context = await p.chromium.launch_persistent_context(
                profile, channel='chromium', headless=True,
                args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'])
            async def route(request):
                url = request.request.url
                if url.startswith('chrome-extension://'):
                    await request.continue_()
                elif '/home/businessData' in url:
                    requests.append(url)
                    await request.fulfill(content_type='application/json', body=json.dumps(
                        {'code': 33202, 'error': {'msg': '商家账号水平鉴权失败'}}),
                        headers={'Access-Control-Allow-Origin': 'https://me.meituan.com',
                                 'Access-Control-Allow-Credentials': 'true'})
                elif '/fixture-resource' in url:
                    await request.fulfill(content_type='text/plain', body='fixture')
                elif url == 'https://me.meituan.com/home':
                    await request.fulfill(content_type='text/html', body='''<!doctype html>
                    <h1>Fictional fixture</h1><script>
                    fetch('/fixture-resource?poiId=123456&partnerId=789012').then(()=>window.ready=true);
                    </script>''')
                else:
                    await request.abort()
            await context.route('**/*', route)
            worker = context.service_workers[0] if context.service_workers else await context.wait_for_event('serviceworker')
            extension_id = worker.url.split('/')[2]
            home = await context.new_page()
            await home.goto('https://me.meituan.com/home')
            await home.wait_for_function('window.ready===true')
            popup = await context.new_page()
            await popup.goto(f'chrome-extension://{extension_id}/popup.html')
            # Activate only this temporary fixture tab, never a real user tab.
            await worker.evaluate("""async()=>{const tabs=await chrome.tabs.query({url:'https://me.meituan.com/home'});await chrome.tabs.update(tabs[0].id,{active:true});}""")
            await popup.evaluate("document.getElementById('refresh').click()")
            try:
                await expect(popup.locator('#collect')).to_be_enabled()
            except AssertionError:
                print(await popup.locator('#identity').text_content())
                print(await worker.evaluate("chrome.tabs.query({active:true,lastFocusedWindow:true}).then(t=>t.map(x=>({id:x.id,url:x.url})))"))
                raise
            await popup.evaluate("document.getElementById('collect').click()")
            await expect(popup.locator('#last')).to_contain_text('33202')
            assert len(requests) == 1
            assert 'poiId=123456' in requests[0] and 'partnerId=789012' in requests[0]
            assert home.url == 'https://me.meituan.com/home'
            stored = await worker.evaluate('chrome.storage.local.get(null)')
            assert '123456' not in json.dumps(stored)
            assert stored['lastReport']['received_all_cards'] is False
            await context.close()
    print('PASS: isolated popup -> worker -> page request; 33202 retained; IDs not stored; URL unchanged')


if __name__ == '__main__':
    asyncio.run(main())
