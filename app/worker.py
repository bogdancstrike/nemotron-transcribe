"""One host worker, one reusable model server; bounded PCM requests and honest timing."""
import fcntl
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
import unicodedata
import wave
from contextlib import contextmanager
from pathlib import Path

import httpx

import db
from db import DATA, claim_job, init_db, recover_jobs, update_job
from media import probe_audio

LOG = logging.getLogger('worker')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
NEMO = os.getenv('NEMO_URL', 'http://nemo:8080').rstrip('/')
OUT = DATA / 'results'


def fmt_time(seconds: float, sep=',') -> str:
    ms = round(float(seconds) * 1000)
    hours, remainder = divmod(ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    sec, milli = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{sec:02d}{sep}{milli:03d}'


def normalized_letters(text):
    return ''.join(c for c in unicodedata.normalize('NFC', text).casefold() if c.isalnum())


def valid_alignment(segment):
    words = segment.get('words')
    text = segment.get('text', '').strip()
    if not text:
        return not words
    if not isinstance(words, list) or not words:
        return False
    previous = -1.0
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get('word'), str) or not word['word'].strip():
            return False
        start, end = word.get('start'), word.get('end')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
            return False
        if start < 0 or end <= start or start < previous:
            return False
        if 'duration_s' in segment and end > segment['duration_s'] + 0.1:
            return False
        previous = start
    # Refuse partial alignment as well as invalid timings. Preserve the raw response in JSON.
    return normalized_letters(' '.join(w['word'] for w in words)) == normalized_letters(text)


def srt_from_segments(segments: list) -> str:
    if not segments or any(not valid_alignment(s) for s in segments):
        return ''
    cues = []
    previous = -1.0
    for segment in segments:
        offset = segment['start_seconds']
        if not math.isfinite(offset) or offset < previous or offset < 0:
            return ''
        previous = offset
        group = []
        for word in segment.get('words') or []:
            if group and (len(group) >= 9 or word['end'] - group[0]['start'] >= 5):
                cues.append((offset + group[0]['start'], offset + max(w['end'] for w in group),
                             ' '.join(w['word'].strip() for w in group)))
                group = []
            group.append(word)
        if group:
            cues.append((offset + group[0]['start'], offset + max(w['end'] for w in group),
                         ' '.join(w['word'].strip() for w in group)))
    return '\n\n'.join(f'{i}\n{fmt_time(start)} --> {fmt_time(end)}\n{text}'
                        for i, (start, end, text) in enumerate(cues, 1)) + ('\n' if cues else '')


def prepare_segments(input_path: str, tempdir: Path, seconds: int) -> list[Path]:
    duration = probe_audio(input_path)
    # PCM16 mono 16 kHz plus filesystem reserve, before decoding long inputs.
    if shutil.disk_usage(tempdir).free < duration * 32000 + int(os.getenv('MIN_FREE_DISK_MB', '1024')) * 1024**2:
        raise RuntimeError('Insufficient disk space for decoded PCM audio')
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
           '-protocol_whitelist', 'file,pipe', '-i', input_path, '-map', '0:a:0', '-vn',
           '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-f', 'segment',
           '-segment_time', str(seconds), '-reset_timestamps', '1', str(tempdir / 'chunk-%05d.wav')]
    process = subprocess.run(cmd, capture_output=True, timeout=int(os.getenv('FFMPEG_TIMEOUT_SECONDS', '3600')), check=False)
    if process.returncode:
        raise RuntimeError('Audio decoding failed: ' + process.stderr.decode('utf-8', 'replace')[-1500:])
    chunks = sorted(tempdir.glob('chunk-*.wav'))
    if not chunks:
        raise RuntimeError('No decodable audio stream found')
    return chunks


def pcm_duration(path):
    # Exact output sample count: no guessed duration when probing fails.
    with wave.open(str(path), 'rb') as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16000 or stream.getsampwidth() != 2:
            raise RuntimeError('Invalid PCM chunk format')
        return stream.getnframes() / stream.getframerate()


def transcribe_chunk(path: Path, lang: str, diarize: bool) -> dict:
    timeout = httpx.Timeout(connect=15, read=float(os.getenv('INFERENCE_TIMEOUT_SECONDS', '3600')), write=180, pool=15)
    payload = {'response_format': 'verbose_json'}
    if lang != 'auto':
        payload['language'] = lang
    if diarize:
        payload['diarization'] = 'true'
    for attempt in range(3):
        try:
            with path.open('rb') as stream, httpx.Client(timeout=timeout) as client:
                response = client.post(NEMO + '/v1/audio/transcriptions', data=payload,
                                       files={'file': (path.name, stream, 'audio/wav')})
            if response.is_error and any(term in response.text.lower() for term in ('out of memory', 'out-of-memory', 'cuda_error_out_of_memory', 'oom')):
                raise RuntimeError('Model ran out of memory. Reduce runtime context/batch or switch to CPU, then restart the engine.')
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get('text'), str):
                raise RuntimeError('Unexpected model response: expected a JSON object with text')
            return result
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            # The server may still be computing; do not submit a duplicate request.
            raise RuntimeError('Model request interrupted after submission; restart/check engine before retrying this file') from exc
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            retriable = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
            if isinstance(exc, httpx.HTTPStatusError):
                retriable = exc.response.status_code == 429 or exc.response.status_code >= 500
            LOG.warning('Transcription attempt %d/3 failed: %s', attempt + 1, exc)
            if not retriable or attempt == 2:
                raise RuntimeError(f'Nemotron backend request failed: {exc}') from exc
            time.sleep((attempt + 1) * 5)
    raise AssertionError('unreachable')


def model_ready():
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(NEMO + '/ready')
        return response.status_code == 200 and response.json().get('ready') is True
    except (httpx.RequestError, ValueError, AttributeError):
        return False


def wait_for_model():
    while not model_ready():
        LOG.info('Waiting for Nemotron engine to load model...')
        time.sleep(10)


def atomic_text(path, text):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def do_job(job: dict):
    id = job['id']
    started = time.perf_counter()
    LOG.info('Job %s: %s', id, job['filename'])
    outdir = OUT / id
    outdir.mkdir(parents=True, exist_ok=True)
    # Never expose leftover exports on a recovered attempt.
    for path in outdir.glob('transcript.*'):
        path.unlink()
    temp_root = DATA / 'tmp'
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=id + '-', dir=temp_root) as temp:
        chunks = prepare_segments(job['input_path'], Path(temp), job['segment_seconds'])
        update_job(id, status='processing', total_segments=len(chunks), completed_segments=0, progress=0)
        segments, cumulative_offset, inference_s = [], 0.0, 0.0
        for n, path in enumerate(chunks):
            duration = pcm_duration(path)
            segment_started = time.perf_counter()
            result = transcribe_chunk(path, job['language'], bool(job['diarize']))
            wall = time.perf_counter() - segment_started
            inference_s += wall
            segment = {'index': n, 'start_seconds': cumulative_offset, 'duration_s': duration,
                       'inference_s': wall, 'rtfx': duration / wall if wall else None,
                       'text': result['text'], 'words': result.get('words') or [],
                       'language': result.get('language'), 'raw_response': result}
            segment['alignment_valid'] = valid_alignment(segment)
            segments.append(segment)
            cumulative_offset += duration
            elapsed = time.perf_counter() - started
            update_job(id, completed_segments=n + 1, progress=min(99, round(100 * (n + 1) / len(chunks))),
                       wall_s=elapsed, inference_s=inference_s, rtfx=cumulative_offset / elapsed)
        combined = '\n\n'.join(s['text'].strip() for s in segments if s['text'].strip())
        atomic_text(outdir / 'transcript.txt', combined + ('\n' if combined else ''))
        srt = srt_from_segments(segments)
        if srt:
            atomic_text(outdir / 'transcript.srt', srt)
        wall_s = time.perf_counter() - started
        metadata = {'job_id': id, 'original_filename': job['filename'], 'language_hint': job['language'],
                    'diarization_requested': bool(job['diarize']), 'speaker_scope': 'chunk',
                    'timestamp_source': 'model_word_alignment' if srt else 'unavailable',
                    'srt_available': bool(srt), 'duration_s': cumulative_offset, 'wall_s': wall_s,
                    'inference_s': inference_s, 'rtfx': cumulative_offset / wall_s,
                    'inference_rtfx': cumulative_offset / inference_s if inference_s else None,
                    'backend': os.getenv('NEMO_MODE', 'cpu'),
                    'segments': segments, 'text': combined}
        atomic_text(outdir / 'transcript.json', json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        update_job(id, status='done', progress=100, duration_s=cumulative_offset, wall_s=wall_s,
                   inference_s=inference_s, rtfx=metadata['rtfx'])
    LOG.info('Finished job %s', id)


@contextmanager
def worker_lock():
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another worker already holds this data directory') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def prune_expired():
    days = int(os.getenv('RETENTION_DAYS', '0'))
    if days <= 0:
        return
    for id in db.expired_jobs(days):
        row = db.delete_job(id)
        if row:
            path = Path(row['input_path'])
            if path.parent.resolve() == (DATA / 'uploads').resolve():
                path.unlink(missing_ok=True)
            shutil.rmtree(OUT / id, ignore_errors=True)


def main():
    with worker_lock():
        init_db()
        recover_jobs()
        OUT.mkdir(parents=True, exist_ok=True)
        # Remove crash leftovers only while holding the exclusive host lock.
        shutil.rmtree(DATA / 'tmp', ignore_errors=True)
        last_prune = 0
        while True:
            if time.monotonic() - last_prune >= 3600:
                prune_expired()
                last_prune = time.monotonic()
            wait_for_model()
            job = claim_job()
            if not job:
                time.sleep(float(os.getenv('POLL_SECONDS', '3')))
                continue
            try:
                do_job(job)
            except Exception as exc:
                LOG.exception('Job failed: %s', job['id'])
                update_job(job['id'], status='failed', error=str(exc)[-1800:])


if __name__ == '__main__':
    main()
