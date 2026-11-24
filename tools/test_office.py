"""Browser smoke test. Run with the project's Python after installing Playwright + Chromium.

Uses a separate server and mocked jobs. Never invokes an AI provider or Unity.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8877'


def main():
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'webapp.server:app',
                               '--host', '127.0.0.1', '--port', '8877'], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                if httpx.get(BASE, timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.3)
        else:
            raise AssertionError('Test server did not start')
        for path in ['/static/office3d.js', '/static/vendor/three.module.js', '/static/vendor/OrbitControls.js']:
            response = httpx.get(BASE + path)
            assert response.status_code == 200, path
            assert 'javascript' in response.headers['content-type'], response.headers
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--enable-unsafe-swiftshader'])
            page = browser.new_page(viewport={'width': 1280, 'height': 900}, reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def mock(route):
                url = route.request.url
                if '/api/events' in url:
                    return route.fulfill(body=': idle\n\n', content_type='text/event-stream')
                if '/api/jobs/current' in url:
                    return route.fulfill(json={'running': False, 'project': None, 'error': None})
                if '/api/projects' in url:
                    return route.fulfill(json=[])
                if '/api/providers' in url:
                    return route.fulfill(json=[])
                return route.continue_()
            page.route('**/api/**', mock)
            page.goto(BASE)
            page.locator('#officeView').click()
            page.locator('#officeCanvas canvas').wait_for(state='visible')
            page.wait_for_function('officeInstance !== null')
            assert page.locator('#pipelinePanel').is_hidden()
            page.evaluate('setStage(1, 1); syncOffice({running: true}); officeInstance.select(1)')
            assert 'working' in page.locator('#officeInfo').inner_text()
            page.evaluate('officeInstance.select(2)')
            assert 'waiting' in page.locator('#officeInfo').inner_text()
            page.evaluate('officeInstance.select(0)')
            assert 'done' in page.locator('#officeInfo').inner_text()
            for i in range(7):
                page.evaluate(f'setStage({i}, {i}); syncOffice({{running:true}}); officeInstance.select({i})')
                assert 'working' in page.locator('#officeInfo').inner_text()
            page.evaluate('setStage(2, -1); syncOffice({running:false}); officeInstance.select(2)')
            assert 'idle' in page.locator('#officeInfo').inner_text()
            page.locator('#officeReset').click()
            # A rendered canvas must contain more than a solid background.
            colors = page.evaluate('''() => {
                officeInstance.reset(); // Read in the same task before WebGL discards the drawing buffer.
                const c = document.querySelector('#officeCanvas canvas');
                const copy = document.createElement('canvas'); copy.width=100; copy.height=60;
                const ctx = copy.getContext('2d'); ctx.drawImage(c,0,0,100,60);
                const data=ctx.getImageData(0,0,100,60).data, colors=new Set();
                for(let i=0;i<data.length;i+=4) colors.add(`${data[i]},${data[i+1]},${data[i+2]}`);
                return colors.size;
            }''')
            assert colors > 40, f'Canvas looks blank ({colors} colors)'
            page.locator('#officePanel').screenshot(path=str(ROOT / 'office-preview.png'))
            page.locator('#pipelineView').click()
            assert page.locator('#officePanel').is_hidden()
            page.locator('#officeView').click()
            assert page.locator('#officeCanvas canvas').count() == 1
            page.reload()
            page.locator('#officeCanvas canvas').wait_for(state='visible')
            page.wait_for_function('officeInstance !== null')
            page.emulate_media(reduced_motion='no-preference')
            page.evaluate('setStage(1,1); syncOffice({running:true}); officeInstance.select(1)')
            page.wait_for_timeout(1800)
            assert 'working' in page.locator('#officeInfo').inner_text()
            assert not errors, errors
            browser.close()
            print(json.dumps({'result': 'PASS', 'canvas_colors': colors,
                              'tests': 'local assets, render, all stages, toggle, reload, animation, reduced motion',
                              'screenshot': 'office-preview.png'}))
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == '__main__':
    main()
