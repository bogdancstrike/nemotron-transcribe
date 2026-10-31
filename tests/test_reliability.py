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
