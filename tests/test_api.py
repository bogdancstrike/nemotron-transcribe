import db
import main
from fastapi.testclient import TestClient


def test_upload_and_status(tmp_path, monkeypatch, wav_bytes):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'DB', tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(main, 'INPUT', tmp_path / 'uploads')
    monkeypatch.setattr(main, 'OUTPUT', tmp_path / 'results')
    with TestClient(main.app) as client:
        upload = client.post('/api/jobs', files={'file': ('Știință.wav', wav_bytes, 'audio/wav')},
                             data={'language': 'ro', 'segment_seconds': '60'})
        assert upload.status_code == 201
        job = client.get('/api/jobs/' + upload.json()['id'])
        assert job.status_code == 200
        assert job.json()['status'] == 'queued'
        assert job.json()['filename'] == 'Știință.wav'
        assert 'input_path' not in job.json()
        assert client.get('/api/jobs/' + upload.json()['id'] + '/download/srt').status_code == 404
        invalid = client.post('/api/jobs', files={'file': ('x.exe', b'bad', 'application/octet-stream')})
        assert invalid.status_code == 400
        empty = client.post('/api/jobs', files={'file': ('x.mp3', b'', 'audio/mpeg')})
        assert empty.status_code == 400
