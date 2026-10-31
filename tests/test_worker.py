import json
import subprocess
import wave
from pathlib import Path

import pytest

import db
import worker


def test_srt_uses_real_timestamps_only():
    segments = [
        {'start_seconds': 0.0, 'text': 'Salut România', 'words': [
            {'word': 'Salut', 'start': 0.5, 'end': 0.9},
            {'word': 'România', 'start': 1.0, 'end': 1.5},
        ]},
        {'start_seconds': 60.0, 'text': 'Bună seara', 'words': [
            {'word': 'Bună', 'start': 0.2, 'end': 0.5},
            {'word': 'seara', 'start': 0.6, 'end': 0.9},
        ]},
    ]
    rendered = worker.srt_from_segments(segments)
    assert '00:00:00,500 --> 00:00:01,500' in rendered
    assert '00:01:00,200 --> 00:01:00,900' in rendered
    assert 'România' in rendered


def test_srt_fails_closed_if_alignment_missing():
    assert worker.srt_from_segments([
        {'start_seconds': 0, 'text': 'Unaligned transcript', 'words': []}
    ]) == ''
    assert worker.srt_from_segments([]) == ''


def test_job_queue_is_persistent_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'DB', tmp_path / 'jobs.sqlite3')
    db.init_db()
    db.create_job('abc123', 'Știință în București.mp4', '/path/not/for/browser.mp4', 'ro', 60, False)
    job = db.get_job('abc123')
    assert 'input_path' not in job
    assert job['status'] == 'queued'
    assert db.claim_job()['id'] == 'abc123'
    assert db.claim_job() is None
    db.update_job('abc123', status='processing', completed_segments=1, progress=20)
    db.recover_jobs()
    assert db.get_job('abc123')['status'] == 'queued'


def test_ffmpeg_segmenting_roundtrip(tmp_path):
    wav = tmp_path / 'input.wav'
    with wave.open(str(wav), 'wb') as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b'\0\0' * 16000 * 3)
    output_dir = tmp_path / 'chunks'
    output_dir.mkdir()
    chunks = worker.prepare_segments(str(wav), output_dir, 1)
    assert len(chunks) >= 2
    assert all(c.exists() for c in chunks)


def test_export_processor_mocked_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'DB', tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(worker, 'OUT', tmp_path / 'results')
    db.init_db()
    input_wav = tmp_path / 'input.wav'
    with wave.open(str(input_wav), 'wb') as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b'\0\0' * 16000 * 2)
    db.create_job('abcd1234', 'Știință.wav', str(input_wav), 'ro', 60, False)

    def fake_response(*args, **kwargs):
        return {'text': 'Țară, școală, înghețată.', 'words': [
            {'word': 'Țară,', 'start': 0.1, 'end': 0.4},
            {'word': 'școală,', 'start': 0.5, 'end': 0.9},
            {'word': 'înghețată.', 'start': 1.0, 'end': 1.5},
        ]}

    monkeypatch.setattr(worker, 'transcribe_chunk', fake_response)
    row = db.claim_job()
    worker.do_job(row)
    assert db.get_job('abcd1234')['status'] == 'done'
    out = tmp_path / 'results' / 'abcd1234'
    assert 'înghețată' in (out / 'transcript.txt').read_text()
    assert (out / 'transcript.srt').is_file()
    result = json.loads((out / 'transcript.json').read_text())
    assert result['language_hint'] == 'ro'
    assert result['srt_available'] is True
