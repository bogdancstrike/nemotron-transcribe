"""Durable analyst annotations, real waveform peaks and bounded audio excerpts."""
from __future__ import annotations

import array
import copy
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

import db

router = APIRouter()
DEFAULT_VALUE = {'names': {}, 'merges': {}, 'overrides': [], 'reviewed': []}


def init_schema():
    with db.connection() as connection:
        connection.execute('''CREATE TABLE IF NOT EXISTS review_annotations (
            job_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL)''')


def _job(id):
    job = db.get_job(id)
    if not job:
        raise HTTPException(404, 'Recording not found')
    return job


def _source(id):
    import main
    _job(id)
    with db.connection() as connection:
        row = connection.execute('SELECT input_path FROM jobs WHERE id=?', (id,)).fetchone()
    source = Path(row['input_path'])
    if source.parent.resolve() != main.INPUT.resolve() or not source.is_file():
        raise HTTPException(404, 'Original recording is missing; upload it again')
    return source


def _result(id):
    import main
    _job(id)
    path = main.OUTPUT / id / 'transcript.json'
    if not path.is_file():
        raise HTTPException(409, 'A completed transcript is required')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise HTTPException(409, 'The saved transcript could not be read. Retry the recording to regenerate it.') from error


def get_annotations(id):
    init_schema()
    with db.connection() as connection:
        row = connection.execute('SELECT * FROM review_annotations WHERE job_id=?', (id,)).fetchone()
    return {'job_id': id, 'revision': row['revision'] if row else 0,
            'value': json.loads(row['value']) if row else copy.deepcopy(DEFAULT_VALUE),
            'updated_at': row['updated_at'] if row else None}


def _speaker(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isdigit() or not 0 <= int(value) <= 9999:
        raise ValueError('A speaker ID from 0 to 9999 is required')
    return str(int(value))


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _interval(item, duration):
    if not isinstance(item, dict) or not all(_number(item.get(key)) for key in ('start', 'end')):
        raise ValueError('A finite start and end time are required')
    start, end = float(item['start']), float(item['end'])
    if not 0 <= start < end <= duration + .15:
        raise ValueError('The selected time range must be inside the recording')
    return {'start': round(start, 6), 'end': round(min(end, duration), 6)}


def coalesce_ranges(ranges):
    merged = []
    for item in sorted(ranges, key=lambda item: item['start']):
        if merged and item['start'] <= merged[-1]['end'] + .001:
            merged[-1]['end'] = max(merged[-1]['end'], item['end'])
        else:
            merged.append(dict(item))
    return merged


def validate_annotations(value, duration):
    if not isinstance(value, dict) or not value.keys() <= DEFAULT_VALUE.keys():
        raise ValueError('Unknown review annotation fields')
    result = copy.deepcopy(DEFAULT_VALUE)
    result.update(value)
    if not isinstance(result['names'], dict) or len(result['names']) > 128:
        raise ValueError('At most 128 speaker names are supported')
    result['names'] = {_speaker(key): name.strip() for key, name in result['names'].items()
                       if isinstance(name, str) and 0 < len(name.strip()) <= 100}
    if len(result['names']) != len(value.get('names', {})):
        raise ValueError('Speaker names must contain 1 to 100 characters')
    if not isinstance(result['merges'], dict) or len(result['merges']) > 128:
        raise ValueError('At most 128 speaker merges are supported')
    result['merges'] = {_speaker(key): _speaker(target) for key, target in result['merges'].items()}
    for source in result['merges']:
        seen, current = set(), source
        while current in result['merges']:
            if current in seen:
                raise ValueError('Speaker merges cannot contain a cycle')
            seen.add(current)
            current = result['merges'][current]
    for key, maximum in [('overrides', 5000), ('reviewed', 10000)]:
        items = result[key]
        if not isinstance(items, list) or len(items) > maximum:
            raise ValueError(f'At most {maximum} {key} intervals are supported')
        result[key] = []
        for item in items:
            interval = _interval(item, duration)
            if key == 'overrides':
                interval['speaker'] = _speaker(item.get('speaker'))
            result[key].append(interval)
    result['reviewed'] = coalesce_ranges(result['reviewed'])
    return result


def resolve_speaker(speaker, value):
    if speaker is None:
        return None
    current = str(speaker)
    for _ in range(129):
        target = value.get('merges', {}).get(current)
        if target is None:
            return current
        current = str(target)
    return current


def effective_speaker(speaker, start, end, value):
    for override in value.get('overrides', []):
        if min(end, override['end']) > max(start, override['start']):
            speaker = override['speaker']
    return resolve_speaker(speaker, value)


def annotate_result(result, annotations):
    """Project analyst edits without mutating stored model output or raw responses."""
    value = annotations.get('value', annotations)
    result = copy.deepcopy(result)
    for segment in result.get('segments', []):
        offset = segment.get('start_seconds', 0)
        for key in ('words', 'playback_words'):
            for word in segment.get(key, []):
                if not _number(word.get('start')) or not _number(word.get('end')):
                    continue
                speaker = effective_speaker(word.get('speaker'), offset + word['start'], offset + word['end'], value)
                if speaker is not None:
                    word['speaker'] = int(speaker)
                    word['speaker_name'] = value.get('names', {}).get(speaker, f'Speaker {speaker}')
    result['speaker_names'] = value.get('names', {})
    result['review_annotations_revision'] = annotations.get('revision', 0)
    return result


def review_progress(value, duration):
    reviewed = sum(item['end'] - item['start'] for item in coalesce_ranges(value.get('reviewed', [])))
    return {'reviewed_s': reviewed, 'duration_s': duration,
            'percent': min(100, round(100 * reviewed / duration, 1)) if duration else 0}


@router.get('/api/jobs/{id}/review-tools')
def review_tools(id: str):
    job = _job(id)
    annotations = get_annotations(id)
    speakers = set(annotations['value']['names'])
    scope = None
    try:
        result = _result(id)
        scope = result.get('speaker_scope')
        for segment in result.get('segments', []):
            for word in segment.get('words', []):
                if word.get('speaker') is not None:
                    speakers.add(str(word['speaker']))
    except HTTPException:
        pass
    for item in annotations['value']['overrides']:
        speakers.add(item['speaker'])
    speakers = sorted({resolve_speaker(speaker, annotations['value']) for speaker in speakers}, key=lambda speaker: int(speaker))
    return {**annotations, 'speakers': [{'id': speaker, 'name': annotations['value']['names'].get(speaker, f'Speaker {speaker}')} for speaker in speakers],
            'speaker_scope': scope, 'progress': review_progress(annotations['value'], job.get('duration_s') or 0)}


@router.put('/api/jobs/{id}/review-tools')
def save_review_tools(id: str, payload: dict):
    job = _job(id)
    if job['status'] != 'done':
        raise HTTPException(409, 'Review annotations can be edited after transcription completes')
    revision = payload.get('revision')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise HTTPException(400, 'A valid review annotation revision is required')
    try:
        value = validate_annotations(payload.get('value'), job.get('duration_s') or 0)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    init_schema()
    with db.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT revision,value FROM review_annotations WHERE job_id=?', (id,)).fetchone()
        current_revision = row['revision'] if row else 0
        if current_revision != revision:
            raise HTTPException(409, 'Another tab changed review annotations. Reload the recording before trying again.')
        connection.execute('''INSERT INTO review_annotations VALUES(?,?,?,?) ON CONFLICT(job_id)
            DO UPDATE SET revision=excluded.revision,value=excluded.value,updated_at=excluded.updated_at''',
            (id, current_revision + 1, json.dumps(value, ensure_ascii=False), db.now()))
    return review_tools(id)


@router.get('/api/jobs/{id}/waveform')
def waveform(id: str, bins: int = Query(1024, ge=64, le=4096)):
    import main
    job, source = _job(id), _source(id)
    duration = job.get('duration_s')
    if not _number(duration) or duration <= 0:
        from media import probe_audio
        duration = probe_audio(source)
    stat = source.stat()
    signature = hashlib.sha256(f'{stat.st_size}:{stat.st_mtime_ns}:{bins}'.encode()).hexdigest()[:16]
    directory = main.OUTPUT / id
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / f'waveform-{signature}.json'
    if cache.is_file():
        try:
            return json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
    frames_per_bin = max(1, math.ceil(duration * 1000 / bins))
    peaks, sample_index = [0.0] * bins, 0
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(source),
               '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '1000', '-f', 's16le', '-']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(180, process.kill)
    timer.daemon = True
    timer.start()
    try:
        leftover = b''
        while chunk := process.stdout.read(65536):
            data = leftover + chunk
            leftover = data[len(data) // 2 * 2:]
            samples = array.array('h')
            samples.frombytes(data[:len(data) // 2 * 2])
            if os.sys.byteorder != 'little':
                samples.byteswap()
            for sample in samples:
                index = min(bins - 1, sample_index // frames_per_bin)
                peaks[index] = max(peaks[index], abs(sample) / 32768)
                sample_index += 1
        if process.wait(timeout=5) != 0:
            raise HTTPException(422, 'Unable to decode waveform within the media limits')
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
    result = {'duration_s': sample_index / 1000, 'peaks': [round(peak, 5) for peak in peaks],
              'sample_rate': 1000, 'source': 'decoded_audio_peak_amplitude'}
    temporary = cache.with_suffix('.tmp-' + os.urandom(4).hex())
    try:
        temporary.write_text(json.dumps(result))
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    return result


@router.get('/api/jobs/{id}/clip')
def clip(id: str, start: float = Query(..., ge=0), end: float = Query(..., gt=0)):
    import main
    job, source = _job(id), _source(id)
    try:
        interval = _interval({'start': start, 'end': end}, job.get('duration_s') or 0)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    duration = interval['end'] - interval['start']
    if duration > 3600:
        raise HTTPException(400, 'Audio excerpts are limited to 60 minutes; download the original recording for longer ranges')
    directory = db.DATA / 'tmp' / 'clips'
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < duration * 32000 + main.MIN_FREE:
        raise HTTPException(507, 'Insufficient disk space for this audio excerpt')
    descriptor, filename = tempfile.mkstemp(prefix='selection-', suffix='.wav', dir=directory)
    os.close(descriptor)
    path = Path(filename)
    try:
        process = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-ss', str(interval['start']),
                                  '-i', str(source), '-t', str(duration), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(path)],
                                 capture_output=True, timeout=180)
        if process.returncode or path.stat().st_size < 44:
            raise HTTPException(422, 'Unable to decode this audio excerpt')
    except subprocess.TimeoutExpired as error:
        path.unlink(missing_ok=True)
        raise HTTPException(422, 'Audio excerpt decoding exceeded the time limit') from error
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    name = f'{Path(job["filename"]).stem[:70]}-{start:.2f}-{end:.2f}.wav'
    return FileResponse(path, media_type='audio/wav', filename=name, background=BackgroundTask(path.unlink, missing_ok=True))


@router.post('/api/jobs/{id}/selection')
def selection(id: str, payload: dict):
    """Resolve browser UTF-16 selections using genuine stored/forced alignment only."""
    import bisect
    from review import TOKEN, align_review, source_words
    _job(id)
    text, start, end = payload.get('text'), payload.get('char_start'), payload.get('char_end')
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise HTTPException(400, 'A transcript of at most 2 million characters is required')
    if any(isinstance(n, bool) or not isinstance(n, int) for n in (start, end)) or not 0 <= start < end:
        raise HTTPException(400, 'Select one or more transcript words')
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))
    first, last = bisect.bisect_left(offsets, start), bisect.bisect_left(offsets, end)
    if first >= len(offsets) or last >= len(offsets) or offsets[first] != start or offsets[last] != end:
        raise HTTPException(400, 'Selection offsets must be valid UTF-16 character boundaries')
    selected = text[first:last]
    mapping = None
    try:
        from alignment import current_alignment
        saved = db.get_correction(id)
        revision = payload.get('revision', saved['revision'] if saved else 0)
        mapping = current_alignment(id, text, revision)
    except ImportError:
        pass
    if mapping is None:
        import main
        path = main.OUTPUT / id / 'transcript.json'
        if not path.is_file():
            raise HTTPException(409, 'A completed transcript with real word timing is required')
        stat = path.stat()
        mapping = align_review(text, source_words(str(path), stat.st_mtime_ns, stat.st_size))
    words = [word for word in mapping.get('words', []) if word['char_start'] < end and word['char_end'] > start]
    token_count = len(list(TOKEN.finditer(selected)))
    complete = token_count > 0 and len(words) >= token_count
    interval = {'start': min(word['start'] for word in words), 'end': max(word['end'] for word in words)} if complete else None
    annotations = get_annotations(id)['value']
    for word in words:
        word['speaker'] = effective_speaker(word.get('speaker'), word['start'], word['end'], annotations)
    return {'text': selected, 'char_start': start, 'char_end': end, 'word_count': token_count,
            'matched_words': len(words), 'complete_timing': complete, 'range': interval, 'words': words,
            'timestamp_source': mapping.get('timestamp_source'),
            'note': 'Playback uses genuine aligned word boundaries.' if complete else 'Some selected words have no reliable timing. Export text or realign this passage before audio actions.'}
