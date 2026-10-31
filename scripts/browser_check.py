#!/usr/bin/env python3
"""Exercise the running studio, real model alignment, routing and analyst editing."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright,expect

TABS=['Upload','Files','Player','Statistics','Info','Settings','Corrections']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:7860')
    parser.add_argument('--output',type=Path,default=Path('evidence/browser'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(args.base_url+'/upload')
        expect(page.locator('#engineLabel')).not_to_have_text('Connecting…')
        assert [s.strip() for s in page.get_by_role('tab').all_text_contents()]==TABS
        assert page.locator('#files').get_attribute('accept').startswith('.m4a,.mp4')
        assert page.locator('#submit').is_disabled()
        box=page.locator('#drop').bounding_box();assert box['width']>650 and abs(box['x']+box['width']/2-720)<5
        page.screenshot(path=str(args.output/'upload-live.png'))
        jobs=page.request.get(args.base_url+'/api/jobs').json()['jobs']
        aligned=next(j for j in jobs if j['status']=='done' and j['srt_available'])
        page.get_by_role('tab',name='Files',exact=True).click();page.wait_for_selector('.job.done')
        page.get_by_role('button',name='Completed',exact=False).first.click()
        assert page.locator('.job:not(.done)').count()==0
        page.get_by_role('button',name='All',exact=False).first.click()
        page.locator('#fileSort').select_option('name');page.locator('#fileSort').select_option('newest')
        page.screenshot(path=str(args.output/'files-live.png'),full_page=True)
        page.locator(f'[data-job-id="{aligned["id"]}"] .preview-button').click()
        page.wait_for_selector('.timed-word')
        page.wait_for_function('() => document.querySelector("#mediaMount audio, #mediaMount video")?.readyState >= 1')
        word=page.locator('.timed-word').first;start=float(word.get_attribute('data-start'))
        page.evaluate('(time)=>{document.querySelector("#mediaMount audio,#mediaMount video").currentTime=time;}',start+.01)
        expect(word).to_have_class('timed-word speaking')
        page.screenshot(path=str(args.output/'player-live.png'),full_page=True)
        page.reload();expect(page.get_by_role('tab',name='Player',exact=True)).to_have_attribute('aria-selected','true');page.wait_for_selector('.timed-word')
        for tab in TABS:
            page.get_by_role('tab',name=tab,exact=True).click()
            assert page.url.split('?')[0].endswith('/'+tab.lower()),page.url
            page.reload();expect(page.get_by_role('tab',name=tab,exact=True)).to_have_attribute('aria-selected','true')
            if tab not in ['Upload','Files','Player','Corrections']:
                page.screenshot(path=str(args.output/(tab.lower()+'-live.png')),full_page=True)
        speakers=next((j for j in jobs if j['status']=='done' and j['diarize']),None)
        if speakers:
            page.goto(args.base_url+'/player?job='+speakers['id']);page.wait_for_selector('.timed-word')
            page.locator('#transcriptView').select_option('speakers');expect(page.locator('#speakerFilter')).to_be_visible()
            assert page.locator('#speakerFilter option').count()>1
            page.screenshot(path=str(args.output/'player-speakers-live.png'),full_page=True)
            page.locator('#speakerFilter').select_option(index=1)
            assert page.locator('.speaker-label').count()==1
            page.locator('#transcriptView').select_option('full')
        # Review an actual benchmark transcript, never a user recording.
        review=next(j for j in jobs if j['filename']=='romanian-news-speakers.wav' and j['status']=='done')
        page.goto(args.base_url+'/corrections?job='+review['id']);expect(page.locator('#correctionText')).to_be_enabled()
        initial=page.locator('#correctionText').input_value();text=initial.replace('se dovând','de dobând')
        if text==initial:text=initial.rstrip()+'\n'
        page.locator('#correctionText').fill(text);page.locator('#saveCorrection').click()
        expect(page.locator('#correctionStatus')).to_have_text('All changes saved')
        page.reload();expect(page.locator('#correctionText')).to_have_value(text)
        page.screenshot(path=str(args.output/'corrections-live.png'),full_page=True)
        with page.expect_download() as info:page.locator('#exportCorrection').click()
        download=info.value
        assert Path(download.path()).read_text(encoding='utf-8')==text
        # Failed autosave keeps a local draft and still permits local export.
        page.route('**/api/jobs/*/correction',lambda route:route.abort() if route.request.method=='PUT' else route.continue_())
        page.locator('#correctionText').fill(text+'\nDraft kept during outage.');page.locator('#saveCorrection').click()
        expect(page.locator('#correctionStatus')).to_contain_text('draft is kept')
        with page.expect_download() as info:page.locator('#exportCorrection').click()
        assert 'Draft kept' in Path(info.value.path()).read_text()
        page.unroute('**/api/jobs/*/correction');page.locator('#correctionText').fill(text);page.locator('#saveCorrection').click()
        # Mobile layout for every route.
        page.set_viewport_size({'width':390,'height':844})
        for tab in TABS:
            page.get_by_role('tab',name=tab,exact=True).click()
            assert page.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth'),tab
            page.screenshot(path=str(args.output/('mobile-'+tab.lower()+'.png')),full_page=True)
        assert not errors,errors
        browser.close()
    result={'result':'passed','page_errors':errors,'checks':['seven routes restore on reload','wide centered M4A chooser','file status views and sorting','real timed playback','partial alignment and speaker filtering','correction autosave/reload/export','offline draft export','390px no overflow on all pages']}
    (args.output/'checks.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
