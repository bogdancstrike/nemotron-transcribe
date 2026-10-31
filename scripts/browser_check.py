#!/usr/bin/env python3
"""Exercise real UI/media, then isolated browser fixtures; never persist fake metrics."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:7860')
    parser.add_argument('--output',type=Path,default=Path('evidence/browser'))
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    issues=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:issues.append(str(error)))
        page.goto(args.base_url)
        page.wait_for_function("() => document.querySelector('#engineLabel').textContent !== 'Connecting…'")
        assert [text.strip() for text in page.get_by_role('tab').all_text_contents()]==['Upload','Files','Player','Statistics','Info']
        assert page.locator('#files').get_attribute('accept').startswith('.m4a,.mp4')
        assert page.locator('#submit').is_disabled()
        page.locator('#drop').focus()
        assert page.evaluate('() => document.activeElement.id')=='drop'
        box=page.locator('#drop').bounding_box()
        assert box['width']>650
        assert abs(box['x']+box['width']/2-720)<5
        page.screenshot(path=str(args.output/'upload-live.png'))
        page.get_by_role('tab',name='Files',exact=True).click()
        page.wait_for_selector('.job.done')
        page.screenshot(path=str(args.output/'files-live.png'),full_page=True)
        # Actual model output + original WAV through the byte-range media endpoint.
        page.get_by_role('button',name='Open player',exact=True).first.click()
        page.wait_for_selector('.timed-word')
        page.wait_for_function('() => document.querySelector("#mediaMount audio, #mediaMount video")?.readyState >= 1')
        first=page.locator('.timed-word').first
        start=float(first.get_attribute('data-start'))
        page.evaluate('(time) => {const m=document.querySelector("#mediaMount audio, #mediaMount video"); m.currentTime=time;}',start+.01)
        expect(first).to_have_class('timed-word speaking')
        page.screenshot(path=str(args.output/'player-live.png'),full_page=True)
        page.get_by_role('tab',name='Statistics',exact=True).click()
        page.screenshot(path=str(args.output/'statistics-live.png'),full_page=True)
        page.get_by_role('tab',name='Info',exact=True).click()
        page.screenshot(path=str(args.output/'info-live.png'),full_page=True)
        # Fixtures validate unavailable alignment and upload navigation without adding real jobs.
        timestamp=datetime.now(timezone.utc).isoformat()
        fixture={'id':'browser-upload','filename':'Știință.m4a','status':'queued','language':'ro','duration_s':2,
                 'segment_seconds':120,'total_segments':0,'completed_segments':0,'progress':0,
                 'wall_s':None,'inference_s':None,'rtfx':None,'created_at':timestamp,'srt_available':False,'error':None}
        posted=[]
        def jobs_route(route):
            if route.request.method=='POST':
                posted.append(True)
                route.fulfill(status=201,json={'id':fixture['id'],'status':'queued'})
            else:
                route.fulfill(json={'jobs':[fixture] if posted else []})
        page.route('**/api/jobs',jobs_route)
        page.get_by_role('tab',name='Upload',exact=True).click()
        page.locator('#files').set_input_files({'name':'Știință.m4a','mimeType':'audio/mp4','buffer':b'test'})
        page.get_by_role('button',name='Transcribe',exact=True).click()
        expect(page.get_by_role('tab',name='Files',exact=True)).to_have_attribute('aria-selected','true')
        expect(page.locator('[data-job-id="browser-upload"]')).to_be_focused()
        page.screenshot(path=str(args.output/'upload-navigation-fixture.png'),full_page=True)
        for tab in ['Upload','Files','Player','Statistics','Info']:
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_role('tab',name=tab,exact=True).click()
            assert page.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth'),tab
            page.screenshot(path=str(args.output/f'mobile-{tab.lower()}.png'),full_page=True)
        assert not issues,issues
        browser.close()
    (args.output/'checks.json').write_text(json.dumps({'result':'passed','page_errors':issues,
        'checks':['five tabs','centered wide chooser','M4A first','keyboard focus','real media decoding',
                  'real aligned word highlight','automatic navigation/focus after upload',
                  '390px no horizontal overflow on every tab'],
        'note':'Screenshots labeled fixture use browser-only data; live screenshots show actual jobs.'},indent=2))
    print('Browser checks passed: real synchronized player, tab navigation, upload redirect and mobile layout.')


if __name__=='__main__':
    main()
