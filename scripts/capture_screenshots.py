#!/usr/bin/env python3
"""Capture real application pages for the GitHub README; no fabricated statistics."""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:7860')
    parser.add_argument('--output',type=Path,default=Path('assets'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
        jobs=page.request.get(args.base_url+'/api/jobs?all=true').json()['jobs']
        aligned=next(j for j in jobs if j['status']=='done' and j['filename']=='romanian-news-60s.wav')
        review=next(j for j in jobs if j['status']=='done' and j['filename']=='romanian-news-speakers.wav')
        for tab in ['upload','files','player','statistics','corrections','info','settings']:
            query='?job='+aligned['id'] if tab=='player' else '?job='+review['id'] if tab=='corrections' else ''
            page.goto(args.base_url+'/'+tab+query)
            expect(page.locator('#engineLabel')).not_to_have_text('Connecting…')
            if tab=='files':
                page.wait_for_selector('.job');assert page.locator('.job').count()<=10
            if tab=='statistics':page.wait_for_selector('#fileStatistics tr')
            if tab=='player':
                page.wait_for_selector('.timed-word');word=page.locator('.timed-word').nth(20)
                page.wait_for_function('() => document.querySelector("#mediaMount audio")?.readyState>=1')
                page.evaluate('(t)=>{document.querySelector("#mediaMount audio").currentTime=t;}',float(word.get_attribute('data-start'))+.01)
                expect(word).to_have_class('timed-word speaking')
            if tab=='corrections':
                expect(page.locator('#correctionText')).to_be_enabled()
                # Apply the explicitly supplied analyst correction to the benchmark review only.
                text=page.locator('#correctionText').input_value().replace('zece puncte zero zero zero','10 000')
                page.locator('#correctionText').fill(text);page.locator('#saveCorrection').click()
                expect(page.locator('#correctionStatus')).to_have_text('All changes saved')
                page.locator('#correctionTitle').click()
                page.wait_for_selector('.review-timed-word')
                page.wait_for_function('() => document.querySelector("#correctionMediaMount audio")?.readyState>=1')
                word=page.locator('.review-timed-word').nth(8)
                page.evaluate('(t)=>{document.querySelector("#correctionMediaMount audio").currentTime=t;}',float(word.get_attribute('data-start'))+.01)
                expect(word).to_have_class('review-timed-word review-current-word')
            if tab=='info':page.wait_for_function('() => document.querySelector("#hostInfo").textContent.includes("Ryzen")')
            page.screenshot(path=str(args.output/(tab+'.png')),full_page=True)
            print('Captured '+tab,flush=True)
            if tab=='corrections':
                word=page.locator('.review-timed-word').nth(8);box=word.bounding_box()
                page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2,button='right')
                expect(page.locator('#reviewContextMenu')).to_be_visible()
                page.screenshot(path=str(args.output/'corrections-menu.png'),full_page=True)
                page.keyboard.press('Escape')
        global_speakers=next((j for j in jobs if j['filename']=='romanian-news-file-speakers.wav' and j['status']=='done'),None)
        if global_speakers:
            page.goto(args.base_url+'/player?job='+global_speakers['id']);page.wait_for_selector('.timed-word')
            page.locator('#transcriptView').select_option('speakers')
            expect(page.locator('#speakerFilter')).to_be_visible()
            page.screenshot(path=str(args.output/'player-speakers.png'),full_page=True)
        browser.close()


if __name__=='__main__':main()
