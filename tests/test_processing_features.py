import array
import json
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import alignment
import db
import main
import worker
from processing import prepare_audio, silence_boundaries, trim_context, normalize_options


def write_wave(path,seconds=3,channels=1):
    samples=array.array('h')
    for n in range(round(seconds*16000)):
        samples.extend([1000*(channel+1) if n%160<80 else -1000*(channel+1) for channel in range(channels)])
    with wave.open(str(path),'wb') as audio:
        audio.setnchannels(channels);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(samples.tobytes())


def test_silence_boundaries_cover_samples_once_and_prefer_pauses():
    boundaries=silence_boundaries(10*16000,4,[(2.5,3.5)])
    assert boundaries==[0,3*16000,7*16000,10*16000]
    assert sum(b-a for a,b in zip(boundaries,boundaries[1:]))==10*16000


def test_context_overlap_is_trimmed_using_actual_times_without_mutating_raw():
    raw={'text':'before core rest','words':[{'word':'before','start':0.,'end':1.},{'word':'core','start':2.,'end':3.},{'word':'rest','start':4.,'end':5.}]}
    result=trim_context(raw,2,4)
    assert result['text']=='core rest'
    assert [(w['start'],w['end']) for w in result['words']]==[(0.,1.),(2.,3.)]
    assert raw['words'][1]['start']==2.


def test_separate_channels_have_exact_shared_timeline_and_distinct_samples(isolated):
    source=isolated/'stereo.wav';write_wave(source,3,2)
    output=isolated/'prepared';output.mkdir()
    result=prepare_audio(source,output,1,{'channel_mode':'separate','overlap_seconds':.2})
    assert result.channels==2 and result.duration_s==3
    assert [part['channel'] for part in result.chunks]==[1,2,1,2,1,2]
    assert result.chunks[2]['start_seconds']==.8
    assert result.chunks[2]['core_start_seconds']==1
    for channel in (1,2):
        restored=b''
        for part in result.chunks:
            if part['channel']!=channel:continue
            with wave.open(str(part['path']),'rb') as audio:
                audio.setpos(round((part['core_start_seconds']-part['start_seconds'])*16000))
                restored+=audio.readframes(round((part['core_end_seconds']-part['core_start_seconds'])*16000))
        assert len(restored)==3*16000*2
        samples=array.array('h');samples.frombytes(restored)
        assert max(samples)==channel*1000


def test_options_reject_invalid_values():
    for options in ({'chunk_mode':'bad'},{'overlap_seconds':True},{'overlap_seconds':11},{'channel_mode':'all'},{'vocabulary':[1]}):
        with pytest.raises(ValueError):normalize_options(options)


def test_worker_pause_checkpoint_and_resume_does_not_repeat_finished_chunk(isolated,monkeypatch):
    source=isolated/'input.wav';write_wave(source)
    db.create_job('pause','input.wav',str(source),'ro',1,False)
    calls=[]
    def reply(*args):
        calls.append(1)
        with db.connection() as connection:connection.execute("UPDATE jobs SET control_action='pause' WHERE id='pause'")
        return {'text':'Salut','words':[{'word':'Salut','start':.1,'end':.5}]}
    monkeypatch.setattr(worker,'transcribe_chunk',reply)
    with pytest.raises(worker.JobPaused):worker.do_job(db.claim_job())
    saved=json.loads((isolated/'results/pause/checkpoint.json').read_text())
    assert len(saved['segments'])==1
    worker.finish_control('pause','paused');assert db.get_job('pause')['status']=='paused'
    with db.connection() as connection:connection.execute("UPDATE jobs SET status='queued' WHERE id='pause'")
    remaining=[]
    monkeypatch.setattr(worker,'transcribe_chunk',lambda *args:(remaining.append(1) or {'text':'Salut','words':[]}))
    worker.do_job(db.claim_job())
    assert len(remaining)==2 and db.get_job('pause')['status']=='done'


def test_cancel_before_submission_preserves_original_recording(isolated,monkeypatch):
    source=isolated/'input.wav';write_wave(source)
    db.create_job('cancel','input.wav',str(source),'ro',1,False)
    job=db.claim_job()
    with db.connection() as connection:connection.execute("UPDATE jobs SET control_action='cancel' WHERE id='cancel'")
    monkeypatch.setattr(worker,'transcribe_chunk',lambda *args:pytest.fail('Cancelled job reached the model'))
    with pytest.raises(worker.JobCancelled):worker.do_job(job)
    worker.finish_control('cancel','cancelled')
    assert source.is_file() and db.get_job('cancel')['status']=='cancelled'


def test_channel_worker_sets_file_wide_channel_ids_without_double_counting_duration(isolated,monkeypatch):
    source=isolated/'stereo.wav';write_wave(source,3,2)
    db.create_job('channels','stereo.wav',str(source),'ro',1,False,processing_options={'channel_mode':'separate'})
    monkeypatch.setattr(worker,'transcribe_chunk',lambda *args:{'text':'Salut','words':[{'word':'Salut','start':.1,'end':.5}]})
    worker.do_job(db.claim_job())
    result=json.loads((isolated/'results/channels/transcript.json').read_text())
    assert result['duration_s']==3 and result['channel_count']==2
    assert [segment['words'][0]['speaker'] for segment in result['segments']]==[1,2,1,2,1,2]
    assert [segment['start_seconds'] for segment in result['segments']]==[0,0,1,1,2,2]


def create_review(isolated,text='Salut nou'):
    uploads=isolated/'uploads';uploads.mkdir(exist_ok=True)
    source=uploads/'review.wav';write_wave(source,2)
    db.create_job('review-align','review.wav',str(source),'ro',60,False,duration_s=2)
    db.update_job('review-align',status='done')
    result=isolated/'results/review-align';result.mkdir(parents=True)
    (result/'transcript.txt').write_text('Salut lume')
    (result/'transcript.json').write_text(json.dumps({'segments':[{'start_seconds':0,'words':[{'word':'Salut','start':.1,'end':.5},{'word':'lume','start':.7,'end':1.2}]}]}))
    return db.save_correction('review-align',text,0)


def test_durable_forced_alignment_links_new_word_only_after_real_ctc_response(isolated,monkeypatch):
    saved=create_review(isolated)
    with TestClient(main.app) as client:
        response=client.post('/api/jobs/review-align/correction/realign',json={'text':saved['text'],'revision':saved['revision']})
        assert response.status_code==202,response.text
    task=alignment.claim_task();real_client=httpx.Client
    def reply(request):
        assert request.url.path=='/v1/audio/alignments'
        assert b'Salut nou' in request.content
        return httpx.Response(200,json={'words':[{'word':'Salut','start':.1,'end':.5},{'word':'nou','start':.7,'end':1.2}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(reply),**kw))
    alignment.run_task(task,'http://model')
    result=alignment.current_alignment('review-align',saved['text'],saved['revision'])
    assert result['timestamp_source']=='forced_ctc_alignment'
    assert result['matched_words']==2 and result['words'][1]['start']==.7
    assert alignment.current_alignment('review-align','A different revision',saved['revision']) is None


def test_alignment_rejects_stale_revision_and_survives_worker_restart(isolated):
    saved=create_review(isolated)
    with TestClient(main.app) as client:
        assert client.post('/api/jobs/review-align/correction/realign',json={'text':'different','revision':saved['revision']}).status_code==400
        response=client.post('/api/jobs/review-align/correction/realign',json={'text':saved['text'],'revision':saved['revision']})
        assert response.status_code==202
    task=alignment.claim_task();alignment.recover_tasks()
    assert alignment.claim_task()['id']==task['id']
    db.save_correction('review-align','newer revision',saved['revision'])
    alignment.run_task(task,'http://not-contacted')
    assert alignment.task_status(task['id'])['status']=='failed'
    assert 'superseded' in alignment.task_status(task['id'])['error']


def test_worker_does_not_duplicate_vocabulary_already_snapshotted_in_guidance(isolated,monkeypatch):
    source=isolated/'terms.wav';write_wave(source,1)
    db.create_job('terms','terms.wav',str(source),'ro',60,False,guidance='Context vocabulary: CUDA. Use only when supported.',processing_options={'vocabulary':['CUDA']})
    prompts=[]
    def reply(*args):
        prompts.append(args[5])
        return {'text':'CUDA','words':[]}
    monkeypatch.setattr(worker,'transcribe_chunk',reply)
    worker.do_job(db.claim_job())
    assert prompts[0].count('CUDA')==1


def test_overlap_guard_compares_speech_evidence_and_words_in_same_core_clock():
    segment={'text':' '.join(['word']*20),'duration_s':2.,'context_offset_seconds':10.,'words':[{'word':'word','start':n*.09,'end':n*.09+.09} for n in range(20)],'raw_response':{'diarization_timestamps':[{'start':0,'end':12.1}]}}
    assert not worker.timing_suspect(segment)


def test_trim_context_removes_explicit_unowned_prefix_and_suffix():
    result=trim_context({'text':'before core, after!','words':[{'word':'before','start':0.,'end':1.},{'word':'core','start':2.,'end':3.},{'word':'after','start':4.,'end':5.}]},2,2)
    assert result['text']=='core,'
    assert [w['word'] for w in result['words']]==['core']
