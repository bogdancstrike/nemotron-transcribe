"""Sample-accurate silence boundaries, overlap ownership and channel extraction."""
import array
import json
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

RATE = 16000
DEFAULT_OPTIONS = {'chunk_mode': 'fixed', 'overlap_seconds': 0, 'channel_mode': 'mix'}


def normalize_options(value):
    if isinstance(value, str):
        try: value = json.loads(value)
        except ValueError as exc: raise ValueError('Processing options must be valid JSON') from exc
    if value is None: value = {}
    if not isinstance(value, dict): raise ValueError('Processing options must be an object')
    result = {**DEFAULT_OPTIONS, **value}
    if result['chunk_mode'] not in {'fixed', 'silence'}: raise ValueError('Chunk mode must be fixed or silence')
    if result['channel_mode'] not in {'mix', 'separate'}: raise ValueError('Channel mode must be mix or separate')
    overlap = result['overlap_seconds']
    if isinstance(overlap, bool) or not isinstance(overlap, (int, float)) or not math.isfinite(overlap) or not 0 <= overlap <= 10:
        raise ValueError('Overlap must be a number from 0 to 10 seconds')
    vocabulary = result.get('vocabulary', [])
    if isinstance(vocabulary, str): vocabulary = [v.strip() for v in vocabulary.splitlines() if v.strip()]
    if not isinstance(vocabulary, list) or len(vocabulary) > 500 or any(not isinstance(v, str) or len(v) > 200 or '<|' in v or '|>' in v for v in vocabulary):
        raise ValueError('Vocabulary must contain at most 500 terms of 200 characters')
    result['vocabulary'] = vocabulary
    return result


def silence_boundaries(total_frames, target_seconds, silences):
    """Partition every sample exactly once, preferring nearby preceding silence midpoints."""
    target = max(1, round(target_seconds * RATE))
    window = min(15 * RATE, target // 4)
    candidates = sorted(round((a + b) * .5 * RATE) for a, b in silences if b > a)
    boundaries = [0]
    while total_frames - boundaries[-1] > target:
        desired = boundaries[-1] + target
        eligible = [n for n in candidates if desired - window <= n <= desired and n > boundaries[-1] + target * .4]
        boundaries.append(max(eligible) if eligible else desired)
    if total_frames > boundaries[-1]: boundaries.append(total_frames)
    # A final feature-frame fragment belongs to the previous chunk, not a new model request.
    if len(boundaries) > 2 and boundaries[-1] - boundaries[-2] < RATE // 10:
        boundaries.pop(-2)
    return boundaries


@dataclass
class PreparedAudio:
    chunks: list
    full_audio: Path
    duration_s: float
    channels: int


def _run(command):
    result = subprocess.run(command, capture_output=True, timeout=3600, check=False)
    if result.returncode:
        raise RuntimeError('Audio preparation failed: ' + result.stderr.decode('utf-8', 'replace')[-1200:])
    return result


def prepare_audio(input_path, directory, seconds, options):
    directory = Path(directory)
    options = normalize_options(options)
    separate = options['channel_mode'] == 'separate'
    full = directory / 'decoded-full.wav'
    from media import probe_audio
    duration = probe_audio(input_path)
    channels_expected = 1
    if separate:
        info = _run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-select_streams','a:0','-show_entries','stream=channels','-of','json',str(input_path)])
        channels_expected = int(json.loads(info.stdout)['streams'][0]['channels'])
        if not 1 <= channels_expected <= 8:raise ValueError('Separate-channel transcription supports up to 8 channels')
    import os
    reserve = int(os.getenv('MIN_FREE_DISK_MB','1024')) * 1024**2
    required = duration * RATE * 2 * channels_expected * (2 + options['overlap_seconds']/max(seconds,1))
    if shutil.disk_usage(directory).free < required + reserve:raise RuntimeError('Insufficient disk space for decoded channels and overlap chunks')
    command = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(input_path), '-map', '0:a:0', '-vn']
    if not separate: command += ['-ac', '1']
    command += ['-ar', str(RATE), '-c:a', 'pcm_s16le', str(full)]
    _run(command)
    with wave.open(str(full), 'rb') as audio:
        channels, frames = audio.getnchannels(), audio.getnframes()
        if not 1 <= channels <= 8: raise ValueError('Separate-channel transcription supports up to 8 channels')
        silences = []
        if options['chunk_mode'] == 'silence':
            result = _run(['ffmpeg', '-hide_banner', '-loglevel', 'info', '-nostdin', '-i', str(full), '-af', 'silencedetect=noise=-35dB:d=0.35', '-f', 'null', '-'])
            start = None
            for match in re.finditer(r'silence_(start|end):\s*([\d.]+)', result.stderr.decode('utf-8', 'replace')):
                if match[1] == 'start': start = float(match[2])
                elif start is not None: silences.append((start, float(match[2]))); start = None
            if start is not None: silences.append((start, frames / RATE))
        boundaries = silence_boundaries(frames, seconds, silences)
        overlap = round(options['overlap_seconds'] * RATE)
        chunks = []
        for index, (begin, end) in enumerate(zip(boundaries, boundaries[1:])):
            context_begin = max(0, begin - overlap)
            audio.setpos(context_begin)
            data = audio.readframes(end - context_begin)
            interleaved = array.array('h'); interleaved.frombytes(data)
            for channel in range(channels):
                path = directory / f'chunk-{index:05d}-ch{channel + 1}.wav'
                with wave.open(str(path), 'wb') as output:
                    output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
                    output.writeframes(interleaved[channel::channels].tobytes())
                chunks.append({'path':path, 'start_seconds':context_begin / RATE, 'core_start_seconds':begin / RATE,
                               'core_end_seconds':end / RATE, 'duration_s':(end-context_begin) / RATE,
                               'channel':channel + 1 if separate else None})
    return PreparedAudio(chunks, full, frames / RATE, channels)


def _canonical(word):
    return ''.join(c for c in word.casefold() if c.isalnum())


def trim_context(result, start_offset, core_duration, previous_text=''):
    """Remove look-behind ownership with genuine word times; never interpolate new timings."""
    import copy
    result = copy.deepcopy(result)
    words = result.get('words') or []
    text = result.get('text', '')
    selected = [w for w in words if isinstance(w, dict) and isinstance(w.get('start'), (int,float)) and isinstance(w.get('end'), (int,float))
                and (w['start'] + w['end']) * .5 >= start_offset and (w['start'] + w['end']) * .5 < start_offset + core_duration]
    if selected:
        # Clip owned text at matched timed-word boundaries on either side. Only a following
        # unowned timed token can justify removing a suffix; unmatched trailing text is kept.
        from review import TOKEN
        tokens = list(TOKEN.finditer(text))
        timed_prefix = [_canonical(w.get('word','')) for w in words]
        import difflib
        matched = {}
        for block in difflib.SequenceMatcher(a=[_canonical(t[0]) for t in tokens], b=timed_prefix, autojunk=False).get_matching_blocks():
            for n in range(block.size):matched[block.b+n]=tokens[block.a+n]
        first_index,last_index=words.index(selected[0]),words.index(selected[-1])
        left=matched[first_index].start() if start_offset>0 and first_index in matched else 0
        right=matched[last_index+1].start() if last_index+1 in matched else len(text)
        text=text[left:right].strip()
    elif start_offset > 0:
        # Without timestamps, remove only an exact lexical suffix/prefix match from the prior core.
        old = list(re.finditer(r'\S+', previous_text)); new = list(re.finditer(r'\S+', text))
        for count in range(min(80, len(old), len(new)), 0, -1):
            if [_canonical(w[0]) for w in old[-count:]] == [_canonical(w[0]) for w in new[:count]]:
                text = text[new[count-1].end():].lstrip(); break
    for word in selected:
        word['start'] = max(0, word['start'] - start_offset)
        word['end'] = min(core_duration, word['end'] - start_offset)
    result['text'] = text
    result['words'] = [w for w in selected if w['end'] > w['start']]
    # Speech-turn evidence remains in the original request clock in raw_response, not core clock.
    result.pop('diarization_timestamps', None)
    return result
