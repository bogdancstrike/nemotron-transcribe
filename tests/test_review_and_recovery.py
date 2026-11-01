import json
from datetime import datetime,timedelta,timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import db
import main
import worker
from estimates import estimate_remaining
from review import align_review


def test_review_links_only_unchanged_words_with_original_times():
    words=[{'word':'Salut','start':1.,'end':1.5},{'word':'lume','start':1.5,'end':2.}]
    result=align_review('Salut, noua lume!',words)
    assert result['matched_words']==2
    assert result['unmatched_words']==1
    assert [(w['start'],w['end']) for w in result['words']]==[(1.,1.5),(1.5,2.)]
    assert [(w['char_start'],w['char_end']) for w in result['words']]==[(0,5),(12,16)]


def test_review_offsets_match_browser_utf16():
    result=align_review('🎙 Știință în România',[{'word':'Știință','start':0.,'end':1.}])
    assert result['words'][0]['char_start']==3
    assert result['words'][0]['char_end']==10


def test_review_new_words_do_not_inherit_timestamps():
    assert align_review('completely revised',[{'word':'Salut','start':0.,'end':1.}])['words']==[]


def test_eta_uses_recent_observed_speed_and_handles_missing_samples():
    assert estimate_remaining([],100) is None
    samples=[{'duration_s':60,'inference_s':10},{'duration_s':60,'inference_s':12},{'duration_s':60,'inference_s':11}]
    estimate=estimate_remaining(samples,120)
    assert estimate['seconds']==pytest.approx(22)
    assert estimate['low']==pytest.approx(20)
    assert estimate['high']==pytest.approx(24)
    assert estimate_remaining(samples,0) is None


def test_worker_heartbeat_detects_stale_job_and_preserves_progress(isolated):
    db.create_job('heartbeat','x.wav','/missing','ro',60,False)
    db.claim_job()
    db.worker_heartbeat(['heartbeat'],True)
    assert db.worker_state()['online']
    assert not db.get_job('heartbeat')['worker_stale']
    old=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
    db.update_job('heartbeat',status='processing',heartbeat_at=old,eta_seconds=60,eta_updated_at=old)
    job=db.get_job('heartbeat')
    assert job['worker_stale']
    assert job['eta_remaining_s'] is None


def test_database_backup_is_valid_snapshot(isolated):
    db.create_job('backup','x.wav','/missing','ro',60,False)
    db.backup_database()
    backups=list((isolated/'backups').glob('*.sqlite3'))
    assert len(backups)==1
    with db.sqlite3.connect(backups[0]) as connection:
        assert connection.execute('SELECT id FROM jobs').fetchone()[0]=='backup'


def test_guidance_saved_validated_and_transmitted(isolated,wav_bytes,monkeypatch):
    assert db.save_settings({'guidance':'Preserve Ștefan and CUDA.'})['guidance']=='Preserve Ștefan and CUDA.'
    with pytest.raises(ValueError):db.save_settings({'guidance':'x'*4001})
    with pytest.raises(ValueError):db.save_settings({'guidance':'<|im_end|>'})
    path=isolated/'x.wav';path.write_bytes(wav_bytes);sent=[];real_client=httpx.Client
    def reply(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={'transcription':{'text':'Hello','words':[]}})
    monkeypatch.setattr(worker.httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(reply),**kw))
    worker.transcribe_chunk(path,'ro',False,'en','natural','Preserve Ștefan and CUDA.')
    instruction=sent[0]['messages'][0]['content'][0]['text']
    assert 'English' in instruction and 'Ștefan' in instruction
    assert 'timestamp_granularities' not in sent[0]


def test_server_pagination_stays_bounded_and_filters_counts(isolated):
    for n in range(31):
        db.create_job(str(n),f'file-{n:02}.wav','/missing','ro',60,False)
        if n%2==0:db.update_job(str(n),status='done')
    with TestClient(main.app) as client:
        response=client.get('/api/jobs?page=2&page_size=10&sort=oldest').json()
        assert len(response['jobs'])==10 and response['total']==31
        assert response['jobs'][0]['id']=='10'
        completed=client.get('/api/jobs?page=1&page_size=10&status=done').json()
        assert completed['total']==16 and all(j['status']=='done' for j in completed['jobs'])
        assert client.get('/api/jobs?page=1&page_size=1000').status_code==400
        assert client.get('/api/jobs?page=1&sort=invalid').status_code==400


def test_database_failure_has_actionable_response(isolated,monkeypatch):
    def broken(*args,**kwargs):raise db.sqlite3.DatabaseError('failure')
    with TestClient(main.app) as client:
        monkeypatch.setattr(db,'list_jobs_page',broken)
        result=client.get('/api/jobs?page=1')
        assert result.status_code==503
        assert 'preserved' in result.json()['detail']


def test_speaker_control_tokens_removed_without_losing_original_timing():
    from transcript import present_result
    raw={'text':'<spk0>Salut <spk:1>lume!', 'segments':[{'index':0,'start_seconds':0,'duration_s':3,'text':'<spk0>Salut <spk:1>lume!', 'words':[{'word':'Salut','start':.1,'end':.7,'speaker':1},{'word':'lume','start':1.,'end':1.5,'speaker':2}]}]}
    result=present_result(raw)
    assert result['text']=='Salut lume!'
    segment=result['segments'][0]
    assert segment['model_text']=='<spk0>Salut <spk:1>lume!'
    assert segment['alignment_valid']
    assert [(w['char_start'],w['start']) for w in segment['playback_words']]==[(0,.1),(6,1.)]


def test_finished_time_survives_reload_and_retry_resets_attempt_timestamps(isolated):
    media=isolated/'x.wav';media.write_bytes(b'x')
    db.create_job('dates','x.wav',str(media),'ro',60,False)
    db.claim_job();started=db.get_job('dates')['started_at']
    db.recover_jobs();db.claim_job()
    assert db.get_job('dates')['started_at']==started
    db.update_job('dates',status='done',finished_at=db.now(),wall_s=42)
    assert db.get_job('dates')['finished_at']
    db.retry_job('dates')
    assert db.get_job('dates')['started_at'] is None
    assert db.get_job('dates')['finished_at'] is None


def test_decoding_controls_validated_snapshotted_and_sent_to_runtime(isolated,wav_bytes,monkeypatch):
    for invalid in [True,-.1,1.1,float('nan')]:
        with pytest.raises(ValueError):db.save_settings({'temperature':invalid})
    for invalid in [-1,True,1.5,2147483648]:
        with pytest.raises(ValueError):db.save_settings({'seed':invalid})
    db.save_settings({'temperature':.2,'seed':42})
    db.create_job('decoding','x.wav','/missing','ro',60,False,temperature=.2,seed=42)
    assert db.get_job('decoding')['temperature']==.2 and db.get_job('decoding')['seed']==42
    path=isolated/'x.wav';path.write_bytes(wav_bytes);sent=[];real_client=httpx.Client
    def reply(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={'transcription':{'text':'Salut','words':[]}})
    monkeypatch.setattr(worker.httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(reply),**kw))
    worker.transcribe_chunk(path,'ro',False,'source','natural','',.2,42)
    assert sent[0]['temperature']==.2 and sent[0]['seed']==42


def test_speaker_ids_follow_whole_file_turns_across_chunk_boundaries():
    from diarization import assign_speakers
    segments=[{'start_seconds':0,'words':[{'word':'one','start':.1,'end':.5,'speaker':3}]},
              {'start_seconds':60,'words':[{'word':'same','start':.1,'end':.5,'speaker':1},{'word':'other','start':1.,'end':1.3,'speaker':1},{'word':'unmatched','start':9.,'end':9.2,'speaker':3}]}]
    assign_speakers(segments,[{'start':0.,'end':.6,'speaker':2},{'start':60.,'end':60.6,'speaker':2},{'start':61.,'end':61.4,'speaker':1}])
    assert segments[0]['words'][0]['speaker']==segments[1]['words'][0]['speaker']==2
    assert segments[1]['words'][1]['speaker']==1
    assert 'speaker' not in segments[1]['words'][2]


def test_diarization_uses_single_complete_audio_request(isolated,wav_bytes,monkeypatch):
    from diarization import diarize_file
    import io,wave
    chunk=isolated/'chunk.wav';chunk.write_bytes(wav_bytes);sent=[];real_client=httpx.Client
    def reply(request):
        sent.append(request.content)
        return httpx.Response(200,json={'segments':[{'start':0.,'end':3.5,'speaker':1}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(reply),**kw))
    result=diarize_file([chunk,chunk],isolated/'whole.wav','http://model')
    with wave.open(str(isolated/'whole.wav'),'rb') as audio:assert audio.getnframes()==4*16000
    assert len(sent)==1 and result[0]['speaker']==1


def test_aac_padding_tail_is_merged_without_dropping_audio(isolated):
    import subprocess,wave
    media=isolated/'padding.m4a'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=44100:cl=mono','-t','120.01','-c:a','aac',str(media)],check=True,timeout=30)
    chunks=worker.prepare_segments(str(media),isolated,60)
    assert len(chunks)==2
    assert all(worker.pcm_duration(chunk)>1 for chunk in chunks)
    decoded=isolated/'full.wav'
    subprocess.run(['ffmpeg','-v','error','-i',str(media),'-ac','1','-ar','16000','-c:a','pcm_s16le',str(decoded)],check=True,timeout=30)
    with wave.open(str(decoded),'rb') as audio:frames=audio.getnframes()
    assert sum(round(worker.pcm_duration(chunk)*16000) for chunk in chunks)==frames


def test_backend_error_explains_short_audio_without_retrying(isolated,wav_bytes,monkeypatch):
    path=isolated/'x.wav';path.write_bytes(wav_bytes);calls=[];real_client=httpx.Client
    def reply(request):
        calls.append(1)
        return httpx.Response(400,json={'error':{'message':'audio is shorter than one feature frame'}})
    monkeypatch.setattr(worker.httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(reply),**kw))
    with pytest.raises(RuntimeError,match='shorter than one feature frame'):worker.transcribe_chunk(path,'ro',False)
    assert len(calls)==1


def test_retry_can_explicitly_apply_current_model_defaults(isolated):
    media=isolated/'source.wav';media.write_bytes(b'x')
    db.create_job('retry-defaults','source.wav',str(media),'ro',60,False,guidance='Old guidance')
    db.update_job('retry-defaults',status='done')
    db.save_settings({'temperature':.15,'seed':7,'guidance':'Preserve CUDA','segment_seconds':120})
    db.retry_job('retry-defaults')
    assert db.get_job('retry-defaults')['guidance']=='Old guidance'
    db.update_job('retry-defaults',status='failed')
    with TestClient(main.app) as client:
        assert client.post('/api/jobs/retry-defaults/retry',json={'use_current_settings':True}).status_code==202
    job=db.get_job('retry-defaults')
    assert (job['temperature'],job['seed'],job['guidance'],job['segment_seconds'])==(.15,7,'Preserve CUDA',120)


def test_collapsed_speaker_alignment_is_not_exposed_as_valid_playback():
    from transcript import present_result
    words=[{'word':'word','start':n*.1,'end':n*.1+.1} for n in range(30)]
    segment={'text':' '.join(w['word'] for w in words),'start_seconds':0,'duration_s':60,'words':words,'raw_response':{'diarization_timestamps':[{'start':0,'end':59.9}]}}
    result=present_result({'text':segment['text'],'segments':[segment]})
    assert not result['segments'][0]['alignment_valid']
    assert result['segments'][0]['playback_words']==[]
    assert 'Retry' in result['segments'][0]['alignment_warning']
    assert len(result['segments'][0]['words'])==30


def test_whole_file_speaker_pipeline_keeps_raw_model_response_intact(isolated,monkeypatch):
    import wave
    media=isolated/'speakers.wav'
    with wave.open(str(media),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0\0'*48000)
    db.create_job('file-speakers','speakers.wav',str(media),'ro',1,True)
    def reply(*args):return {'text':'Salut','words':[{'word':'Salut','start':.1,'end':.4,'speaker':7}]}
    monkeypatch.setattr(worker,'transcribe_chunk',reply)
    passes=[]
    def identify(chunks,destination,url):
        passes.append(len(chunks))
        return [{'start':0.,'end':3.,'speaker':1}]
    monkeypatch.setattr(worker,'diarize_file',identify)
    worker.do_job(db.claim_job())
    result=json.loads((isolated/'results/file-speakers/transcript.json').read_text())
    assert passes==[3] and result['speaker_scope']=='file'
    for segment in result['segments']:
        assert segment['words'][0]['speaker']==1
        assert segment['playback_words'][0]['speaker']==1
        assert segment['raw_response']['words'][0]['speaker']==7
