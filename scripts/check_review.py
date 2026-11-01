#!/usr/bin/env python3
"""Verify editable word seeking, live highlighting and menu against real stored alignment."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE='http://127.0.0.1:7860'
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    jobs=page.request.get(BASE+'/api/jobs?all=true').json()['jobs']
    job=next(j for j in jobs if j['filename']=='romanian-news-60s.wav' and j['status']=='done')
    page.goto(BASE+'/corrections?job='+job['id'])
    editor=page.locator('#correctionText');expect(editor).to_be_enabled()
    page.wait_for_selector('.review-timed-word')
    page.wait_for_function('() => document.querySelector("#correctionMediaMount audio").readyState>=1')
    word=page.locator('.review-timed-word').nth(3)
    start=float(word.get_attribute('data-start'));end=float(word.get_attribute('data-end'))
    box=word.bounding_box();page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2)
    page.wait_for_function('(t)=>Math.abs(document.querySelector("#correctionMediaMount audio").currentTime-t)<.08',arg=start)
    page.evaluate('(t)=>{const m=document.querySelector("#correctionMediaMount audio");m.currentTime=t;}',(start+end)/2)
    expect(word).to_have_class('review-timed-word review-current-word')
    # Highlight follows genuine time updates while playing, without moving the editing caret.
    caret=editor.evaluate('(e)=>e.selectionStart')
    page.evaluate('()=>document.querySelector("#correctionMediaMount audio").play()')
    page.wait_for_function('()=>!document.querySelector("#correctionMediaMount audio").paused')
    page.wait_for_function('()=>document.querySelectorAll(".review-current-word").length>0')
    assert editor.evaluate('(e)=>e.selectionStart')==caret
    page.evaluate('()=>document.querySelector("#correctionMediaMount audio").pause()')
    page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2,button='right')
    expect(page.locator('#reviewContextMenu')).to_be_visible()
    expect(page.locator('#reviewContextPlay')).to_be_enabled()
    page.locator('#reviewContextPlay').click()
    page.wait_for_function('(t)=>Math.abs(document.querySelector("#correctionMediaMount audio").currentTime-t)<.5',arg=start)
    page.evaluate('()=>document.querySelector("#correctionMediaMount audio").pause()')
    # Native editor selection stays usable for replacement and keyboard menu access.
    original=editor.input_value()
    editor.fill(original+'\nUnmatchednewword')
    expect(page.locator('#reviewSyncStatus')).to_contain_text('linked to audio')
    assert 'Unmatchednewword' not in page.locator('.review-timed-word').all_text_contents()
    editor.fill(original);page.locator('#saveCorrection').click()
    expect(page.locator('#correctionStatus')).to_have_text('All changes saved')
    page.wait_for_selector('.review-timed-word')
    editor.focus();page.keyboard.press('Shift+F10');expect(page.locator('#reviewContextMenu')).to_be_visible()
    page.keyboard.press('Escape');expect(page.locator('#reviewContextMenu')).to_be_hidden()
    # Tagged model output is readable in full Player, without disturbing word offsets.
    tagged=next(j for j in jobs if j['filename']=='romanian-news-numbers-speakers.wav')
    page.goto(BASE+'/player?job='+tagged['id']);page.wait_for_selector('.timed-word')
    text=page.locator('#syncedTranscript').inner_text()
    assert '<spk' not in text and '10.000' in text
    global_job=next(j for j in jobs if j['filename']=='romanian-news-file-speakers.wav' and j['status']=='done')
    result=page.request.get(BASE+'/api/jobs/'+global_job['id']+'/download/json').json()
    assert result['speaker_scope']=='file'
    assert all(len({w.get('speaker') for w in segment['words'] if w.get('speaker')})==3 for segment in result['segments'])
    import difflib,statistics
    first,second=result['segments']
    errors_seconds=[]
    matcher=difflib.SequenceMatcher(a=[w['word'] for w in first['words']],b=[w['word'] for w in second['words']],autojunk=False)
    for block in matcher.get_matching_blocks():
        for n in range(block.size):errors_seconds.append(abs(second['start_seconds']+second['words'][block.b+n]['start']-first['words'][block.a+n]['start']-60))
    assert len(errors_seconds)>100 and statistics.median(errors_seconds)<.2
    page.goto(BASE+'/player?job='+global_job['id']);page.wait_for_selector('.timed-word')
    page.locator('#transcriptView').select_option('speakers')
    expect(page.locator('#speakerFilter option')).to_have_count(4)
    page.goto(BASE+'/files');page.wait_for_selector('.file-metrics')
    for label in ['Added at','Started','Ended','Process time','RTFx','RTF']:
        assert label in page.locator('.file-metrics').first.inner_text()
    page.goto(BASE+'/settings');expect(page.locator('#modelTemperature')).to_have_value('0')
    page.get_by_role('button',name='Reliable',exact=True).click()
    expect(page.locator('#parallelLevel')).to_have_value('1');expect(page.locator('#defaultChunk')).to_have_value('60')
    assert not errors,errors
    page.screenshot(path='evidence/review-check-settings.png',full_page=True)
    browser.close()
Path('evidence/review-check.json').write_text(json.dumps({'passed':True,'errors':errors,'checks':['word click seeks real media','spoken word highlights live','caret remains stable','right-click play','keyboard contextual menu','new words stay untimed','speaker tokens hidden with numeric formatting preserved','all file date and speed fields','reliable processing preset','three file-wide speaker IDs across both chunks','repeated-audio word times agree within 0.2s median']},indent=2))
print('Editable sync and operational UI checks passed.')
