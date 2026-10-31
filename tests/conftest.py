import io
import wave

import pytest

import db
import main
import worker


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'DB', tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(main, 'INPUT', tmp_path / 'uploads')
    monkeypatch.setattr(main, 'OUTPUT', tmp_path / 'results')
    monkeypatch.setattr(worker, 'DATA', tmp_path)
    monkeypatch.setattr(worker, 'OUT', tmp_path / 'results')
    db.init_db()
    return tmp_path


@pytest.fixture
def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 32000)
    return buffer.getvalue()
