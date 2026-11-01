"""One host worker, one reusable model server; bounded PCM requests and honest timing."""
import fcntl
import difflib
import base64
import re
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
import shutil
import signal
import threading
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
from estimates import estimate_remaining
from settings import CAPACITY, LANGUAGES
from media import probe_audio
from transcript import readable_text
from diarization import assign_speakers, diarize_file, MAX_SPEAKER_SECONDS
from processing import normalize_options, DEFAULT_OPTIONS, prepare_audio, trim_context

LOG = logging.getLogger('worker')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)
NEMO = os.getenv('NEMO_URL', 'http://nemo:8080').rstrip('/')
OUT = DATA / 'results'
STOP = threading.Event()


class JobInterrupted(RuntimeError):
    pass


class JobPaused(JobInterrupted):
    pass


class JobCancelled(JobInterrupted):
    pass


class BackendDisconnected(RuntimeError):
    pass


def fmt_time(seconds: float, sep=',') -> str:
    ms = round(float(seconds) * 1000)
    hours, remainder = divmod(ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    sec, milli = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{sec:02d}{sep}{milli:03d}'


def normalized_letters(text):
    return ''.join(c for c in unicodedata.normalize('NFC', text).casefold() if c.isalnum())


def timing_suspect(segment):
    """Reject a collapsed alignment when the model itself detects much later speech."""
    words=segment.get('words') or []
    turns=(segment.get('raw_response') or {}).get('diarization_timestamps') or []
    if len(words)<20 or not turns:return False
    speech_end=max(0,max((t.get('end',0) for t in turns if isinstance(t,dict) and isinstance(t.get('end'),(int,float))),default=0)-segment.get('context_offset_seconds',0))
    if 'context_offset_seconds' in segment:speech_end=min(segment.get('duration_s',speech_end),speech_end)
    aligned_end=max((w.get('end',0) for w in words if isinstance(w,dict) and isinstance(w.get('end'),(int,float))),default=0)
    return speech_end-aligned_end>5 and aligned_end<speech_end*.75


def valid_alignment(segment):
    words = segment.get('words')
    text = segment.get('text', '').strip()
    if timing_suspect(segment):return False
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


def playback_words(segment):
    """Map exact transcript tokens to genuine model words; unmatched text stays untimed."""
    if timing_suspect(segment):return []
    text = segment.get('text','')
    tokens = list(re.finditer(r"[^\W_]+(?:['’\-][^\W_]+)*", text, re.UNICODE))
    words = [word for word in segment.get('words',[]) if isinstance(word,dict) and
             isinstance(word.get('word'),str) and all(isinstance(word.get(k),(int,float)) and not isinstance(word[k],bool) and math.isfinite(word[k]) for k in ('start','end')) and
             0 <= word['start'] < word['end'] <= segment.get('duration_s',float('inf')) + .1]
    source = [normalized_letters(token.group()) for token in tokens]
    timed = [normalized_letters(word['word']) for word in words]
    result = []
    for block in difflib.SequenceMatcher(a=source,b=timed,autojunk=False).get_matching_blocks():
        for n in range(block.size):
            token, word = tokens[block.a+n], words[block.b+n]
            result.append({'word':token.group(), 'char_start':len(text[:token.start()].encode('utf-16-le'))//2, 'char_end':len(text[:token.end()].encode('utf-16-le'))//2,
                           'start':word['start'],'end':word['end'], **({'speaker':word['speaker']} if 'speaker' in word else {})})
    return result


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
    cues.sort(key=lambda cue:cue[0])
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
    # AAC/container padding can create a final fragment shorter than the encoder minimum.
    # Merge it into the previous chunk, retaining every decoded sample.
    if len(chunks)>1 and pcm_duration(chunks[-1])<.1:
        previous,tail=chunks[-2:]
        with wave.open(str(previous),'rb') as source:
            params=source.getparams();frames=source.readframes(source.getnframes())
        with wave.open(str(tail),'rb') as source:frames+=source.readframes(source.getnframes())
        merged=previous.with_suffix('.merged.wav')
        with wave.open(str(merged),'wb') as target:target.setparams(params);target.writeframes(frames)
        merged.replace(previous);tail.unlink();chunks.pop()
    return chunks


def pcm_duration(path):
    # Exact output sample count: no guessed duration when probing fails.
    with wave.open(str(path), 'rb') as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16000 or stream.getsampwidth() != 2:
            raise RuntimeError('Invalid PCM chunk format')
        return stream.getnframes() / stream.getframerate()


def transcribe_chunk(path: Path, lang: str, diarize: bool, output_language="source", number_style="natural", guidance="", temperature=0.0, seed=None) -> dict:
    timeout = httpx.Timeout(connect=15, read=float(os.getenv('INFERENCE_TIMEOUT_SECONDS', '3600')), write=180, pool=15)
    payload = {'response_format': 'verbose_json'}
    if lang != 'auto':
        payload['language'] = lang
    translation = output_language not in {'source', lang}
    if translation:
        payload['target_language'] = output_language
    elif diarize:
        payload['diarization'] = 'true'
    custom = temperature != 0 or seed is not None or bool(guidance.strip()) or (not translation and (diarize or number_style == 'digits'))
    instruction = 'Transcribe the audio faithfully in its original language, with punctuation and capitalization. Preserve names, quantities, units and the exact numeric values. Do not translate, summarize, or add information.'
    if lang != 'auto':
        instruction += f' The speech is in {LANGUAGES.get(lang,lang)}.'
    if number_style == 'digits':
        instruction += ' Write numeric quantities in conventional numeric form rather than spelling separator punctuation. For example, Romanian "zece mii de lei" is "10 000 lei". Preserve actual decimal values and units.'
    if diarize:
        instruction += ' Include all spoken words and verbal fillers. Return the transcript without speaker control tags; speaker identification is performed separately.'
    if translation:
        instruction = f'Translate the speech into {LANGUAGES.get(output_language,output_language)}. Preserve the facts, names, quantities and numeric values. Return only the translated transcript.'
    if guidance.strip():
        instruction += ' Additional transcription guidance: ' + guidance.strip()
    chat = {'model':'salm','messages':[{'role':'user','content':[
        {'type':'text','text':instruction},
        {'type':'input_audio','input_audio':{'data':base64.b64encode(path.read_bytes()).decode('ascii'),'format':'wav'}}]}],
        **({'timestamp_granularities':['word']} if not translation else {})} if custom else None
    if chat is not None:
        chat['temperature'] = temperature
        if seed is not None:chat['seed'] = seed
    for attempt in range(3):
        try:
            with path.open('rb') as stream, httpx.Client(timeout=timeout) as client:
                if custom:
                    response = client.post(NEMO + '/v1/chat/completions', json=chat)
                else:
                    response = client.post(NEMO + ('/v1/audio/translations' if translation else '/v1/audio/transcriptions'), data=payload,
                                           files={'file': (path.name, stream, 'audio/wav')})
            if response.is_error and any(term in response.text.lower() for term in ('out of memory', 'out-of-memory', 'cuda_error_out_of_memory', 'oom')):
                raise RuntimeError('Model ran out of memory. Reduce runtime context/batch or switch to CPU, then restart the engine.')
            response.raise_for_status()
            result = response.json()
            if custom:
                raw = result
                result = raw.get('transcription', {})
                if isinstance(result,dict):result = {**result, '_raw_chat_response':raw}
            if not isinstance(result, dict) or not isinstance(result.get('text'), str):
                raise RuntimeError('Unexpected model response: expected a JSON object with text')
            return result
        except (httpx.ReadError, httpx.RemoteProtocolError) as exc:
            raise BackendDisconnected('Engine connection was interrupted') from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            # The server may still be computing; do not submit a duplicate request.
            raise RuntimeError('Model request interrupted after submission; restart/check engine before retrying this file') from exc
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            retriable = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
            if isinstance(exc, httpx.HTTPStatusError):
                retriable = exc.response.status_code == 429 or exc.response.status_code >= 500
            LOG.warning('Transcription attempt %d/3 failed: %s', attempt + 1, exc)
            if not retriable or attempt == 2:
                detail=str(exc)
                if isinstance(exc,httpx.HTTPStatusError):
                    try:
                        body=exc.response.json();error=body.get('error',body.get('detail',body))
                        detail=error.get('message',str(error)) if isinstance(error,dict) else str(error)
                    except (ValueError,AttributeError):detail=exc.response.text[:800] or detail
                    detail=f'HTTP {exc.response.status_code}: {detail[:800]}'
                raise RuntimeError(f'Nemotron backend request failed: {detail}') from exc
            time.sleep((attempt + 1) * 5)
    raise AssertionError('unreachable')


def model_ready():
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(NEMO + '/ready')
        return response.status_code == 200 and response.json().get('ready') is True
    except (httpx.RequestError, ValueError, AttributeError):
        return False


def wait_for_model(timeout=None):
    deadline = time.monotonic() + timeout if timeout else None
    while not model_ready():
        if STOP.is_set():raise JobInterrupted('Worker is stopping')
        if deadline and time.monotonic() >= deadline:
            raise RuntimeError('Model did not recover in time. Check engine logs, then retry the recording.')
        LOG.info('Waiting for Nemotron engine to load model...')
        time.sleep(10)


def atomic_text(path, text):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def with_engine_recovery(operation, id, stage):
    for recovery in range(3):
        try:return operation()
        except (BackendDisconnected,httpx.ReadError,httpx.RemoteProtocolError) as exc:
            # A disconnected request may still be computing on a live engine.
            if recovery==2 or model_ready():
                raise RuntimeError('Engine connection interrupted. Check model state, then retry this recording.') from exc
            update_job(id,stage='waiting_for_engine',eta_seconds=None,error='Model restarted; waiting to resume the current stage')
            wait_for_model(float(os.getenv('ENGINE_RESTART_TIMEOUT_SECONDS','900')))
            update_job(id,stage=stage,error=None)
    raise RuntimeError('Model disconnected repeatedly')


def check_control(id):
    with db.connection() as connection:
        row=connection.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
    if row:
        action=dict(row).get('control_action')
        if action=='pause':raise JobPaused('Paused at a saved chunk boundary')
        if action=='cancel':raise JobCancelled('Cancelled; original recording and saved chunks are preserved')


def finish_control(id,status):
    with db.connection() as connection:
        connection.execute("UPDATE jobs SET status=?,stage=?,control_action=NULL,eta_seconds=NULL,error=NULL,finished_at=?,updated_at=? WHERE id=?",(status,status,db.now() if status=='cancelled' else None,db.now(),id))


def do_job(job: dict):
    id=job['id'];started=time.perf_counter()
    check_control(id)
    update_job(id,stage='preparing',heartbeat_at=db.now())
    LOG.info('Job %s: %s',id,job['filename'])
    outdir=OUT/id;outdir.mkdir(parents=True,exist_ok=True)
    from workspace_store import archive_run
    archive_run(id,OUT)
    for path in outdir.glob('transcript.*'):path.unlink()
    options=normalize_options(job.get('processing_options'))
    advanced=any(options[key]!=value for key,value in DEFAULT_OPTIONS.items())
    guidance=job.get('guidance','')
    if options['vocabulary'] and 'Context vocabulary:' not in guidance:
        guidance=(guidance+' Context vocabulary (use only if supported by the audio): '+', '.join(options['vocabulary']))[:4000]
    temp_root=DATA/'tmp';temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=id+'-',dir=temp_root) as temp:
        if advanced:
            audio=prepare_audio(job['input_path'],Path(temp),job['segment_seconds'],options)
            plan=audio.chunks;file_duration=audio.duration_s;diarization_chunks=[audio.full_audio]
        else:
            chunks=prepare_segments(job['input_path'],Path(temp),job['segment_seconds'])
            plan=[];offset=0.
            for path in chunks:
                duration=pcm_duration(path)
                plan.append({'path':path,'start_seconds':offset,'core_start_seconds':offset,'core_end_seconds':offset+duration,'duration_s':duration,'channel':None})
                offset+=duration
            file_duration=offset;diarization_chunks=chunks
        core_durations=[part['core_end_seconds']-part['core_start_seconds'] for part in plan]
        update_job(id,status='processing',stage='transcribing',total_segments=len(plan))
        segments=[];inference_s=0.;source=Path(job['input_path']).stat()
        signature={'size':source.st_size,'mtime_ns':source.st_mtime_ns,'language':job['language'],'diarize':bool(job['diarize']),
                   'segment_seconds':job['segment_seconds'],'output_language':job.get('output_language','source'),
                   'number_style':job.get('number_style','natural'),'backend':os.getenv('NEMO_MODE','cpu'),'patch_commit':os.getenv('NEMO_PATCH_COMMIT','')}
        if job.get('number_style','natural')!='natural' or job['diarize']:
            signature['transcription_policy']='file-speakers-v1' if job['diarize'] else 'readable-numbers-v1'
        if guidance.strip():signature['guidance']=guidance.strip()
        if job.get('temperature',0):signature['temperature']=job['temperature']
        if job.get('seed') is not None:signature['seed']=job['seed']
        if advanced or options['vocabulary']:signature['processing_options']=options
        checkpoint=outdir/'checkpoint.json';prior_wall=0.
        try:
            saved=json.loads(checkpoint.read_text());saved['signature'].setdefault('output_language','source');saved['signature'].setdefault('number_style','natural')
            if saved['signature']==signature and isinstance(saved['segments'],list) and len(saved['segments'])<=len(plan):
                for index,segment in enumerate(saved['segments']):
                    if (segment['index']!=index or abs(segment['start_seconds']-plan[index]['core_start_seconds'])>1/16000
                        or abs(segment['duration_s']-core_durations[index])>1/16000 or segment.get('channel')!=plan[index]['channel']):
                        raise ValueError('Checkpoint does not match PCM segments')
                segments=saved['segments'];inference_s=sum(part['inference_s'] for part in segments);prior_wall=float(saved['wall_s'])
                LOG.info('Resuming %s after %d completed segments',id,len(segments))
        except (OSError,ValueError,KeyError,TypeError):LOG.info('No compatible checkpoint for %s; starting at the first segment',id)
        if checkpoint.exists() and not segments:checkpoint.replace(outdir/f'checkpoint-superseded-{time.time_ns()}.json')
        update_job(id,completed_segments=len(segments),progress=min(99,round(100*len(segments)/len(plan))))
        for n,part in enumerate(plan):
            if n<len(segments):continue
            check_control(id)
            if STOP.is_set():raise JobInterrupted('Worker is restarting; saved chunks will resume')
            update_job(id,stage='transcribing',segment_started_at=db.now())
            segment_started=time.perf_counter();interval_started=db.now();request_guidance=guidance
            previous=next((segment for segment in reversed(segments) if segment.get('channel')==part['channel']),None)
            if options['overlap_seconds'] and previous:
                request_guidance=(guidance+' Context from the previous passage for spelling and continuity only; do not add it unless audible: '+previous['text'][-600:])[:4000]
            raw=with_engine_recovery(lambda:transcribe_chunk(part['path'],job['language'],bool(job['diarize']),job.get('output_language','source'),job.get('number_style','natural'),request_guidance,job.get('temperature',0.),job.get('seed')),id,'transcribing')
            db.record_interval(id,interval_started,db.now());wall=time.perf_counter()-segment_started;inference_s+=wall
            context_offset=part['core_start_seconds']-part['start_seconds'];duration=core_durations[n]
            result=trim_context(raw,context_offset,duration,previous['text'] if previous else '') if advanced else raw
            segment={'index':n,'start_seconds':part['core_start_seconds'],'duration_s':duration,'inference_s':wall,
                     'rtfx':duration/wall if wall else None,'text':readable_text(result['text']),'words':[dict(w) for w in result.get('words') or []],
                     'language':result.get('language'),'raw_response':raw}
            if advanced:segment.update(channel=part['channel'],context_offset_seconds=context_offset,request_start_seconds=part['start_seconds'])
            if part['channel'] is not None:
                for word in segment['words']:word['speaker']=part['channel']
            segment['alignment_valid']=valid_alignment(segment);segment['playback_words']=playback_words(segment);segments.append(segment)
            elapsed=prior_wall+time.perf_counter()-started
            atomic_text(checkpoint,json.dumps({'signature':signature,'segments':segments,'wall_s':elapsed},ensure_ascii=False))
            estimate=estimate_remaining(segments,sum(core_durations[n+1:]))
            eta={'eta_seconds':estimate['seconds'],'eta_low_s':estimate['low'],'eta_high_s':estimate['high'],'eta_samples':estimate['samples'],'eta_updated_at':db.now()} if estimate else {'eta_seconds':None}
            processed=max(part['core_end_seconds'] for part in plan[:n+1])
            update_job(id,**eta,completed_segments=n+1,progress=min(99,round(100*(n+1)/len(plan))),wall_s=elapsed,inference_s=inference_s,rtfx=processed/elapsed)
            check_control(id)
        check_control(id)
        if job['diarize'] and options['channel_mode']!='separate':
            if STOP.is_set():raise JobInterrupted('Worker is restarting; saved chunks will resume')
            if file_duration>MAX_SPEAKER_SECONDS+.1:raise RuntimeError('File-wide speaker identification supports recordings up to 60 minutes; disable it for longer recordings')
            update_job(id,stage='identifying_speakers',eta_seconds=None)
            speaker_started=time.perf_counter();speaker_interval=db.now()
            turns=with_engine_recovery(lambda:diarize_file(diarization_chunks,Path(temp)/'speakers.wav',NEMO),id,'identifying_speakers')
            inference_s+=time.perf_counter()-speaker_started;db.record_interval(id,speaker_interval,db.now());assign_speakers(segments,turns)
            for segment in segments:segment['playback_words']=playback_words(segment)
        else:turns=[]
        check_control(id);update_job(id,stage='exporting',eta_seconds=None)
        combined='\n\n'.join(segment['text'].strip() for segment in segments if segment['text'].strip())
        atomic_text(outdir/'transcript.txt',combined+('\n' if combined else ''));srt=srt_from_segments(segments)
        if srt:atomic_text(outdir/'transcript.srt',srt)
        wall_s=prior_wall+time.perf_counter()-started
        metadata={'job_id':id,'original_filename':job['filename'],'language_hint':job['language'],'diarization_requested':bool(job['diarize']),
                  'speaker_scope':'file' if job['diarize'] or options['channel_mode']=='separate' else None,'speaker_turns':turns,
                  'speaker_source':'audio_channels' if options['channel_mode']=='separate' else 'model' if job['diarize'] else None,
                  'channel_count':audio.channels if advanced else 1,'processing_options':options,
                  'output_language':job.get('output_language','source'),'number_style':job.get('number_style','natural'),
                  'guidance':guidance,'temperature':job.get('temperature',0.),'seed':job.get('seed'),'timestamp_source':'model_word_alignment' if srt else 'unavailable',
                  'srt_available':bool(srt),'duration_s':file_duration,'wall_s':wall_s,'inference_s':inference_s,'rtfx':file_duration/wall_s,
                  'inference_rtfx':file_duration/inference_s if inference_s else None,'backend':os.getenv('NEMO_MODE','cpu'),'segments':segments,'text':combined}
        atomic_text(outdir/'transcript.json',json.dumps(metadata,ensure_ascii=False,indent=2)+'\n');checkpoint.unlink(missing_ok=True)
        update_job(id,status='done',stage='done',finished_at=db.now(),eta_seconds=None,progress=100,duration_s=file_duration,wall_s=wall_s,inference_s=inference_s,rtfx=metadata['rtfx'])
        with db.connection() as connection:connection.execute('UPDATE jobs SET control_action=NULL WHERE id=?',(id,))
    LOG.info('Finished job %s',id)


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


def error_code(exc):
    message=str(exc).lower()
    if 'out of memory' in message:return 'MODEL_OOM'
    if 'missing' in message or 'no such file' in message:return 'FILE_MISSING'
    if 'decod' in message:return 'INVALID_MEDIA'
    if 'space' in message:return 'DISK_FULL'
    if 'model' in message or 'engine' in message or 'backend' in message:return 'ENGINE_ERROR'
    return 'PROCESSING_ERROR'


def safe_backup():
    try:db.backup_database()
    except (OSError,db.sqlite3.Error):LOG.exception('Database backup could not be written; uploads and queue remain active')


def main():
    STOP.clear()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:STOP.set())
    with worker_lock():
        init_db()
        safe_backup()
        recover_jobs()
        import alignment
        alignment.init_schema();alignment.recover_tasks()
        OUT.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(DATA / 'tmp', ignore_errors=True)
        last_prune=last_heartbeat=0
        ready=False
        with ThreadPoolExecutor(max_workers=CAPACITY,thread_name_prefix='file') as pool:
            futures={};alignment_future=None
            while futures or alignment_future or not STOP.is_set():
                if alignment_future is not None and alignment_future.done():
                    alignment_future.result();alignment_future=None
                for future in list(futures):
                    if future.done():
                        job=futures.pop(future)
                        try:future.result()
                        except JobPaused:
                            finish_control(job['id'],'paused')
                        except JobCancelled:
                            finish_control(job['id'],'cancelled')
                        except JobInterrupted:
                            update_job(job['id'],status='queued',stage='recovering',eta_seconds=None,error=None)
                        except Exception as exc:
                            LOG.exception('Job failed: %s',job['id'])
                            update_job(job['id'],status='failed',stage='failed',finished_at=db.now(),eta_seconds=None,error_code=error_code(exc),error=str(exc)[-1800:])
                if time.monotonic()-last_prune>=3600:
                    prune_expired();safe_backup();last_prune=time.monotonic()
                ready=model_ready() if time.monotonic()-last_heartbeat>=2 else ready
                if time.monotonic()-last_heartbeat>=2:
                    db.worker_heartbeat([job['id'] for job in futures.values()],ready,STOP.is_set());last_heartbeat=time.monotonic()
                settings=db.get_settings()
                if not STOP.is_set() and not futures and alignment_future is None and ready:
                    task=alignment.claim_task()
                    if task:alignment_future=pool.submit(alignment.run_task,task,NEMO)
                if not STOP.is_set() and alignment_future is None and not settings['queue_paused'] and len(futures)<settings['parallel_jobs'] and ready:
                    job=claim_job(settings['parallel_jobs'])
                    if job:
                        futures[pool.submit(do_job,job)]=job
                        continue
                STOP.wait(.5) if not STOP.is_set() else time.sleep(.2)
        db.worker_heartbeat([],False,True)


if __name__ == '__main__':
    main()
