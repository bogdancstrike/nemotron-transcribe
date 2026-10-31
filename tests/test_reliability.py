import concurrent.futures
import json
import subprocess
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import db
import main
import worker


def test_reject_corrupt_media_before_queue(isolated):
    with TestClient(main.app) as client:
        response = client.post('/api/jobs', files={'file': ('bad.wav', b'RIFFfake', 'audio/wav')})
        assert response.status_code == 400
        assert client.get('/api/jobs').json()['jobs'] == []
        assert list((isolated / 'uploads').iterdir()) == []


def test_limits_origins_and_delete(isolated, wav_bytes, monkeypatch):
    with TestClient(main.app) as client:
        assert client.post('/api/jobs', headers={'Origin': 'https://evil.example'},
                           files={'file': ('x.wav', wav_bytes)}).status_code == 403
        monkeypatch.setattr(main, 'MAX_UPLOAD', 4)
        assert client.post('/api/jobs', files={'file': ('x.wav', wav_bytes)}).status_code == 413
        monkeypatch.setattr(main, 'MAX_UPLOAD', 100000)
        created = client.post('/api/jobs', files={'file': ('Știință în București.wav', wav_bytes)}).json()
        id = created['id']
        assert client.get('/api/jobs/' + id).json()['duration_s'] == 2
        db.claim_job()
        assert client.delete('/api/jobs/' + id).status_code == 409
        db.update_job(id, status='done')
        out = isolated / 'results' / id
        out.mkdir()
        (out / 'transcript.txt').write_text('Țară, școală, înghețată', encoding='utf-8')
        downloaded = client.get(f'/api/jobs/{id}/download/txt')
        assert downloaded.text == 'Țară, școală, înghețată'
        assert "filename*=utf-8''" in downloaded.headers['content-disposition']
        assert client.delete('/api/jobs/' + id).status_code == 204
        assert not out.exists()
        assert list((isolated / 'uploads').iterdir()) == []


@pytest.mark.parametrize('word', [
    {'word':'Salut','start':None,'end':1}, {'word':'Salut','start':float('nan'),'end':1},
    {'word':'Salut','start':0,'end':float('inf')}, {'word':'Salut','start':1,'end':0},
    {'word':'Salut','start':-1,'end':1}, {'word':'Salut','end':1},
    {'word':'Salut','start':False,'end':1}, {'word':'Salut','start':'0','end':'1'},
    {'word':'Salut','start':0,'end':10}, {}, 'bad',
])
def test_invalid_alignment_never_breaks_text_export(word):
    assert worker.srt_from_segments([{'start_seconds':0, 'duration_s':2, 'text':'Salut', 'words':[word]}]) == ''


def test_partial_alignment_omitted():
    assert worker.srt_from_segments([{'start_seconds':0, 'text':'Salut România',
                                     'words':[{'word':'Salut','start':0,'end':1}]}]) == ''


def test_single_claim_and_complete_recovery(isolated):
    for n in range(5):
        db.create_job(str(n), 'x.wav', '/tmp/x.wav', 'ro', 60, False)
    with concurrent.futures.ThreadPoolExecutor(5) as pool:
        claimed = list(pool.map(lambda _: db.claim_job(), range(5)))
    assert len([x for x in claimed if x]) == 1
    id = next(x['id'] for x in claimed if x)
    db.update_job(id, status='processing', completed_segments=3, total_segments=5, progress=60)
    db.recover_jobs()
    recovered = db.get_job(id)
    assert recovered['completed_segments'] == recovered['total_segments'] == recovered['progress'] == 0
    assert recovered['status'] == 'queued'


def test_second_worker_cannot_recover(isolated):
    with worker.worker_lock():
        with pytest.raises(RuntimeError, match='Another worker'):
            with worker.worker_lock():
                pytest.fail('Second worker acquired lock')


@pytest.mark.parametrize('statuses, expected', [([400],1),([401],1),([413],1),([422],1),([429,503,200],3),([500,500,500],3)])
def test_bounded_http_retry(isolated, wav_bytes, monkeypatch, statuses, expected):
    wav = isolated / 'x.wav'
    wav.write_bytes(wav_bytes)
    calls = []
    real_client = httpx.Client
    def respond(request):
        code = statuses[len(calls)]
        calls.append(code)
        return httpx.Response(code, json={'text':'Bună'} if code == 200 else {'error':'temporarily unavailable'})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(worker.httpx, 'Client', lambda **kwargs: real_client(transport=transport, **kwargs))
    monkeypatch.setattr(worker.time, 'sleep', lambda _: None)
    if statuses[-1] == 200:
        assert worker.transcribe_chunk(wav, 'ro', False)['text'] == 'Bună'
    else:
        with pytest.raises(RuntimeError):
            worker.transcribe_chunk(wav, 'ro', False)
    assert len(calls) == expected


def test_oom_has_no_retry(isolated, wav_bytes, monkeypatch):
    wav = isolated / 'x.wav'
    wav.write_bytes(wav_bytes)
    calls = []
    real_client = httpx.Client
    def respond(request):
        calls.append(1)
        return httpx.Response(500, json={'error':'CUDA out of memory'})
    monkeypatch.setattr(worker.httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(RuntimeError, match='out of memory'):
        worker.transcribe_chunk(wav, 'ro', False)
    assert len(calls) == 1


def test_exact_offsets_and_hour_pipeline(isolated, monkeypatch):
    """Real 60-minute MP4/FFmpeg; fake words/backend, so no model performance claim."""
    media = isolated / 'hour.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=16000:cl=mono',
                    '-t','3600','-c:a','aac','-b:a','16k',str(media)], check=True, timeout=90)
    db.create_job('hour', 'Știință.mp4', str(media), 'ro', 60, False)
    calls = []
    def respond(path, *args):
        calls.append(worker.pcm_duration(path))
        return {'text':'Țară, școală, înghețată.', 'words':[
            {'word':'Țară,','start':0.1,'end':0.4},
            {'word':'școală,','start':0.5,'end':0.9},
            {'word':'înghețată.','start':1.,'end':1.5}]}
    monkeypatch.setattr(worker, 'transcribe_chunk', respond)
    worker.do_job(db.claim_job())
    result = json.loads((isolated / 'results/hour/transcript.json').read_text())
    assert len(calls) == 60
    assert abs(sum(calls) - 3600) < 0.1
    offset = 0
    for segment in result['segments']:
        assert segment['start_seconds'] == pytest.approx(offset, abs=1/16000)
        offset += segment['duration_s']
    assert result['speaker_scope'] == 'chunk'
    assert result['srt_available']
    assert db.get_job('hour')['progress'] == 100


def test_statistics_use_weighted_time_and_ignore_failures(isolated):
    for id, duration, wall in [('a',60,120),('b',600,60)]:
        db.create_job(id,'x.wav','/tmp/x.wav','ro',60,False)
        db.update_job(id,status='done',duration_s=duration,wall_s=wall,inference_s=wall / 2)
    db.create_job('failed','x.wav','/tmp/x.wav','ro',60,False)
    db.update_job('failed',status='failed',duration_s=99999,wall_s=1)
    stats = db.statistics()
    assert stats['audio_s'] == 660
    assert stats['rtfx'] == pytest.approx(660 / 180)
    assert stats['rtf'] == pytest.approx(180 / 660)
    assert stats['inference_rtfx'] == pytest.approx(660 / 90)
    assert stats['counts']['done'] == 2
    assert stats['counts']['failed'] == 1


@pytest.mark.parametrize('extension', ['m4a', 'mp4', 'mp4a'])
def test_aac_container_upload(isolated, extension):
    file = isolated / 'sample.m4a'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1',
                    '-c:a','aac',str(file)], check=True, timeout=15)
    with TestClient(main.app) as client:
        response = client.post('/api/jobs', files={'file':(f'Știință.{extension}', file.read_bytes(), 'audio/mp4')})
        assert response.status_code == 201
        assert client.get('/api/jobs/' + response.json()['id']).json()['duration_s'] == pytest.approx(1, abs=0.1)


def test_player_media_byte_ranges(isolated, wav_bytes):
    with TestClient(main.app) as client:
        id = client.post('/api/jobs', files={'file':('Știință.wav',wav_bytes)}).json()['id']
        response = client.get(f'/api/jobs/{id}/media', headers={'Range':'bytes=0-43'})
        assert response.status_code == 206
        assert response.content == wav_bytes[:44]
        assert response.headers['content-range'] == f'bytes 0-43/{len(wav_bytes)}'
        assert 'inline' in response.headers['content-disposition']
        assert client.get('/api/jobs/missing/media').status_code == 404


def test_retry_failed_and_done_keeps_upload_and_rejects_active(isolated, wav_bytes):
    with TestClient(main.app) as client:
        id = client.post('/api/jobs', files={'file':('recording.m4a',wav_bytes)}).json()['id']
        assert client.post(f'/api/jobs/{id}/retry').status_code == 409
        db.update_job(id,status='failed',error='Engine restarted')
        assert client.post(f'/api/jobs/{id}/retry').status_code == 202
        assert db.get_job(id)['error'] is None
        assert len(list((isolated / 'uploads').iterdir())) == 1
        db.update_job(id,status='done')
        assert client.post(f'/api/jobs/{id}/retry').status_code == 202
        assert client.post('/api/jobs/missing/retry').status_code == 404


def test_resume_after_crash_skips_completed_chunks(isolated, wav_bytes, monkeypatch):
    media=isolated / 'input.wav'
    with wave.open(str(media),'wb') as file:
        file.setnchannels(1);file.setsampwidth(2);file.setframerate(16000)
        file.writeframes(b'\0\0' * 16000 * 3)
    db.create_job('resume','input.wav',str(media),'ro',1,False)
    calls=[]
    def crash(path,*args):
        calls.append(path.name)
        if len(calls)==2:
            raise RuntimeError('Simulated worker crash')
        return {'text':'Bună','words':[{'word':'Bună','start':.1,'end':.4}]}
    monkeypatch.setattr(worker,'transcribe_chunk',crash)
    with pytest.raises(RuntimeError,match='Simulated'):
        worker.do_job(db.claim_job())
    assert (isolated/'results/resume/checkpoint.json').is_file()
    db.recover_jobs()
    resumed=[]
    def success(path,*args):
        resumed.append(path.name)
        return {'text':'Bună','words':[{'word':'Bună','start':.1,'end':.4}]}
    monkeypatch.setattr(worker,'transcribe_chunk',success)
    worker.do_job(db.claim_job())
    assert len(resumed)==2
    assert resumed[0]=='chunk-00001.wav'
    assert db.get_job('resume')['status']=='done'
    result=json.loads((isolated/'results/resume/transcript.json').read_text())
    assert len(result['segments'])==3
    assert not (isolated/'results/resume/checkpoint.json').exists()


def test_restart_outage_resumes_current_chunk_once(isolated,wav_bytes,monkeypatch):
    media=isolated/'x.wav';media.write_bytes(wav_bytes)
    db.create_job('outage','x.wav',str(media),'ro',60,False)
    calls=[]
    def respond(*args):
        calls.append(1)
        if len(calls)==1:
            raise worker.BackendDisconnected('Engine restarted')
        return {'text':'Salut','words':[{'word':'Salut','start':.1,'end':.8}]}
    waited=[]
    monkeypatch.setattr(worker,'transcribe_chunk',respond)
    monkeypatch.setattr(worker,'model_ready',lambda:False)
    monkeypatch.setattr(worker,'wait_for_model',lambda timeout=None:waited.append(timeout))
    worker.do_job(db.claim_job())
    assert len(calls)==2
    assert waited==[900.0]
    assert db.get_job('outage')['status']=='done'


def test_settings_persist_and_bound_parallel_claims(isolated):
    db.save_settings({'parallel_jobs':4,'input_language':'auto','output_language':'ro','segment_seconds':60})
    assert db.get_settings()['parallel_jobs']==4
    for n in range(6):db.create_job(str(n),'x.wav','/tmp/x','auto',60,False)
    assert all(db.claim_job(4) for _ in range(4))
    assert db.claim_job(4) is None
    with pytest.raises(ValueError):db.save_settings({'parallel_jobs':999})
    with pytest.raises(ValueError):db.save_settings({'parallel_jobs':True})
    with pytest.raises(ValueError):db.save_settings({'unknown':1})
    with TestClient(main.app) as client:
        assert client.get('/api/settings').json()['settings']['output_language']=='ro'
        assert client.put('/api/settings',json={'parallel_jobs':2}).status_code==200
        assert client.put('/api/settings',json={'output_language':'en','diarize':True}).status_code==400


def test_upload_defaults_input_output_languages(isolated,wav_bytes):
    db.save_settings({'input_language':'auto','output_language':'en','segment_seconds':60})
    with TestClient(main.app) as client:
        response=client.post('/api/jobs',files={'file':('x.m4a',wav_bytes)})
        assert response.status_code==201
        job=client.get('/api/jobs/'+response.json()['id']).json()
        assert job['language']=='auto'
        assert job['output_language']=='en'
        assert job['segment_seconds']==60
        assert client.post('/api/jobs',files={'file':('x.m4a',wav_bytes)},data={'language':'ro','output_language':'en','diarize':'true'}).status_code==400


def test_translation_uses_documented_endpoint(isolated,wav_bytes,monkeypatch):
    path=isolated/'x.wav';path.write_bytes(wav_bytes)
    real_client=httpx.Client;requests=[]
    def reply(request):
        requests.append(request)
        return httpx.Response(200,json={'text':'Hello world','task':'translate','language':'en','duration':2})
    monkeypatch.setattr(worker.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(reply),**kwargs))
    result=worker.transcribe_chunk(path,'ro',False,'en')
    assert result['text']=='Hello world'
    assert requests[0].url.path=='/v1/audio/translations'
    body=requests[0].content.decode(errors='replace')
    assert 'target_language' in body
    assert 'diarization' not in body
    assert not result.get('words')


def test_concurrent_throughput_counts_overlap_once(isolated):
    for id in ('a','b'):
        db.create_job(id,'x.wav','/tmp/x','ro',60,False)
        db.update_job(id,status='done',duration_s=60,inference_s=10,wall_s=11)
        db.record_interval(id,'2026-10-31T10:00:00+00:00','2026-10-31T10:00:10+00:00')
    result=db.model_throughput()
    assert result['model_busy_s']==10
    assert result['model_throughput_rtfx']==12
    assert result['model_traced_files']==2


def test_downloader_verifies_and_atomically_replaces_manifest(tmp_path,monkeypatch):
    import importlib.util
    import hashlib
    import sys
    spec=importlib.util.spec_from_file_location('download_model',Path(__file__).resolve().parents[1]/'scripts/download_model.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    models=tmp_path/'models';models.mkdir()
    file=models/'test.gguf';file.write_bytes(b'GGUFtest')
    lock={'model_repo':'test','model_revision':'test','files':[{'filename':file.name,'size':8,'sha256':hashlib.sha256(b'GGUFtest').hexdigest()}]}
    (tmp_path/'runtime.lock.json').write_text(json.dumps(lock))
    manifest=models/'manifest.json';manifest.write_text('{}');manifest.chmod(0o444)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(sys,'argv',['download_model','--models-dir',str(models),'--verify-only'])
    module.main()
    assert json.loads(manifest.read_text())['model_repo']=='test'


def test_partial_alignment_maps_only_real_matching_words():
    segment={'text':'Salut, lume minunată!','duration_s':3,'words':[
        {'word':'Salut','start':.1,'end':.4,'speaker':1},
        {'word':'lume','start':.5,'end':.8,'speaker':2},
        {'word':'altceva','start':1,'end':1.5}]}
    words=worker.playback_words(segment)
    assert [word['word'] for word in words]==['Salut','lume']
    assert [(word['start'],word['end']) for word in words]==[(.1,.4),(.5,.8)]
    assert words[1]['speaker']==2
    assert worker.srt_from_segments([{'start_seconds':0,**segment}])==''


def test_canonical_page_routes_survive_reload(isolated):
    with TestClient(main.app) as client:
        assert client.get('/',follow_redirects=False).headers['location']=='/upload'
        for page in ('upload','files','player','statistics','info','settings'):
            response=client.get('/'+page)
            assert response.status_code==200
            assert '<!doctype html>' in response.text.lower()


@pytest.mark.parametrize('parallel',[1,2,4,8,16])
def test_parallel_limits_remain_atomic(isolated,parallel):
    for n in range(20):db.create_job(str(n),'x.wav','/tmp/x','ro',60,False)
    with concurrent.futures.ThreadPoolExecutor(20) as pool:
        claimed=list(pool.map(lambda _:db.claim_job(parallel),range(20)))
    assert sum(row is not None for row in claimed)==parallel


def test_corrections_are_revisioned_and_preserve_original(isolated,wav_bytes):
    with TestClient(main.app) as client:
        id=client.post('/api/jobs',files={'file':('Știință.wav',wav_bytes)}).json()['id']
        assert client.get(f'/api/jobs/{id}/correction').status_code==409
        out=isolated/'results'/id;out.mkdir();(out/'transcript.txt').write_text('Text original.\n',encoding='utf-8')
        db.update_job(id,status='done')
        original=client.get(f'/api/jobs/{id}/correction').json()
        assert original['revision']==0
        updated=client.put(f'/api/jobs/{id}/correction',json={'text':'Țară, școală, înghețată.','revision':0})
        assert updated.status_code==200
        assert updated.json()['revision']==1
        conflict=client.put(f'/api/jobs/{id}/correction',json={'text':'Another tab','revision':0})
        assert conflict.status_code==409
        assert client.get(f'/api/jobs/{id}/correction/download/txt').text=='Țară, școală, înghețată.'
        assert client.get(f'/api/jobs/{id}/download/txt').text=='Text original.\n'
        exported=client.get(f'/api/jobs/{id}/correction/download/json').json()
        assert exported['timing_source'] is None
        assert client.get(f'/api/jobs/{id}/correction/download/srt').status_code==404
        assert client.put(f'/api/jobs/{id}/correction',json={'text':'x','revision':True}).status_code==400
        assert client.delete(f'/api/jobs/{id}').status_code==204
        assert db.get_correction(id) is None


def test_correction_conflict_is_atomic(isolated):
    db.create_job('review','x.wav','/tmp/x','ro',60,False)
    def save(text):
        try:return db.save_correction('review',text,0)['revision']
        except ValueError:return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results=list(pool.map(save,['First review','Second review']))
    assert results.count(1)==1
    assert results.count('conflict')==1
