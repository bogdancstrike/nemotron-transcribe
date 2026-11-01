import copy
import io
import json
import math
import struct
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import main
import review_tools


def client_for(isolated, wav_bytes):
    source = main.INPUT / 'review-source.wav'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(wav_bytes)
    db.create_job('review-source', 'Review Știință.wav', str(source), 'ro', 60, False, duration_s=2)
    db.update_job('review-source', status='done')
    output = main.OUTPUT / 'review-source'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'transcript.json').write_text(json.dumps({'text':'Salut lume', 'speaker_scope':'file', 'segments':[{'index':0,'start_seconds':0,'duration_s':2,'text':'Salut lume','words':[{'word':'Salut','start':.1,'end':.5,'speaker':1},{'word':'lume','start':.7,'end':1.2,'speaker':2}]}]}))
    app = FastAPI()
    app.include_router(review_tools.router)
    return TestClient(app)


def test_review_annotations_merge_overrides_and_revision_conflicts(isolated, wav_bytes):
    client = client_for(isolated, wav_bytes)
    initial = client.get('/api/jobs/review-source/review-tools').json()
    assert initial['revision'] == 0 and len(initial['speakers']) == 2
    value = {'names':{'1':'Ștefan'},'merges':{'2':'1'},'overrides':[{'start':.1,'end':.5,'speaker':3}],
             'reviewed':[{'start':0,'end':.7},{'start':.5,'end':1.0}]}
    saved = client.put('/api/jobs/review-source/review-tools',json={'revision':0,'value':value})
    assert saved.status_code == 200
    data = saved.json()
    assert data['revision'] == 1 and data['progress']['percent'] == 50
    assert data['value']['reviewed'] == [{'start':0.,'end':1.}]
    assert client.put('/api/jobs/review-source/review-tools',json={'revision':0,'value':value}).status_code == 409
    assert client.get('/api/jobs/review-source/review-tools').json()['value']['names']['1'] == 'Ștefan'


def test_invalid_speaker_cycles_and_ranges_are_refused(isolated,wav_bytes):
    client = client_for(isolated,wav_bytes)
    for value in [{'merges':{'1':'2','2':'1'}},{'overrides':[{'start':0,'end':3,'speaker':1}]},{'names':{'1':''}}, {'reviewed':[{'start':False,'end':1}]}]:
        assert client.put('/api/jobs/review-source/review-tools',json={'revision':0,'value':value}).status_code == 400


def test_projection_preserves_original_model_data_and_applies_latest_override():
    original={'segments':[{'start_seconds':60,'words':[{'word':'Salut','start':.1,'end':.5,'speaker':1}],
                           'playback_words':[{'word':'Salut','start':.1,'end':.5,'speaker':1}],
                           'raw_response':{'words':[{'speaker':1}]}}]}
    snapshot=copy.deepcopy(original)
    annotations={'revision':2,'value':{'names':{'3':'Ana'},'merges':{'2':'3'},'overrides':[{'start':60,'end':61,'speaker':2}],'reviewed':[]}}
    projected=review_tools.annotate_result(original,annotations)
    assert original==snapshot
    assert projected['segments'][0]['words'][0]['speaker']==3
    assert projected['segments'][0]['playback_words'][0]['speaker_name']=='Ana'
    assert projected['segments'][0]['raw_response']['words'][0]['speaker']==1


def test_multiword_selection_resolves_real_bounds_and_refuses_new_word_timing(isolated,wav_bytes):
    client=client_for(isolated,wav_bytes)
    selected=client.post('/api/jobs/review-source/selection',json={'text':'Salut lume','char_start':0,'char_end':10}).json()
    assert selected['complete_timing'] and selected['word_count']==2
    assert selected['range']=={'start':.1,'end':1.2}
    changed=client.post('/api/jobs/review-source/selection',json={'text':'Salut noua lume','char_start':0,'char_end':15}).json()
    assert not changed['complete_timing'] and changed['range'] is None
    assert changed['text']=='Salut noua lume'


def test_unicode_selection_offsets_match_browser_utf16(isolated,wav_bytes):
    client=client_for(isolated,wav_bytes)
    selected=client.post('/api/jobs/review-source/selection',json={'text':'🎙 Salut lume','char_start':3,'char_end':13}).json()
    assert selected['text']=='Salut lume' and selected['complete_timing']
    assert client.post('/api/jobs/review-source/selection',json={'text':'🎙 Salut lume','char_start':1,'char_end':13}).status_code==400


def test_waveform_comes_from_actual_audio_and_cache_changes_with_source(isolated,wav_bytes):
    client=client_for(isolated,wav_bytes)
    path=main.INPUT/'review-source.wav'
    samples=[int(12000*math.sin(2*math.pi*100*n/16000)) for n in range(32000)]
    with wave.open(str(path),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
        output.writeframes(struct.pack('<'+'h'*len(samples),*samples))
    response=client.get('/api/jobs/review-source/waveform?bins=64')
    assert response.status_code==200
    waveform=response.json()
    assert waveform['source']=='decoded_audio_peak_amplitude'
    assert len(waveform['peaks'])==64 and max(waveform['peaks'])>.2
    assert waveform['duration_s']==pytest.approx(2,abs=.01)
    assert client.get('/api/jobs/review-source/waveform?bins=64').json()==waveform
    assert client.get('/api/jobs/review-source/waveform?bins=100000').status_code==422


def test_audio_excerpt_retains_requested_duration_and_removes_temporary_file(isolated,wav_bytes):
    client=client_for(isolated,wav_bytes)
    response=client.get('/api/jobs/review-source/clip?start=0.25&end=1.25')
    assert response.status_code==200 and response.headers['content-type']=='audio/wav'
    with wave.open(io.BytesIO(response.content),'rb') as audio:
        assert audio.getnframes()==16000
    assert not list((isolated/'tmp/clips').glob('*.wav'))
    assert client.get('/api/jobs/review-source/clip?start=1&end=8').status_code==400
    assert client.get('/api/jobs/review-source/clip?start=1&end=0.5').status_code==400


def test_selection_prefers_genuine_forced_alignment_for_corrected_word(isolated,wav_bytes,monkeypatch):
    client=client_for(isolated,wav_bytes)
    import alignment
    db.save_correction('review-source','Corectat',0)
    def mapped(job_id,text,revision):
        assert (job_id,text,revision)==('review-source','Corectat',1)
        return {'words':[{'char_start':0,'char_end':8,'start':.3,'end':1.2}], 'timestamp_source':'forced_ctc_alignment'}
    monkeypatch.setattr(alignment,'current_alignment',mapped)
    result=client.post('/api/jobs/review-source/selection',json={'text':'Corectat','char_start':0,'char_end':8}).json()
    assert result['complete_timing'] and result['range']=={'start':.3,'end':1.2}
    assert result['timestamp_source']=='forced_ctc_alignment'
