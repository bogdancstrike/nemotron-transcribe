"""Durable analyst-text CTC alignment tasks, admitted by the model worker when idle."""
import hashlib
import json
import math
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

import db
from review import align_review, source_words

router = APIRouter()


def init_schema():
    with db.connection() as connection:
        connection.execute('''CREATE TABLE IF NOT EXISTS alignment_tasks (
            id TEXT PRIMARY KEY,job_id TEXT NOT NULL,text TEXT NOT NULL,text_hash TEXT NOT NULL,
            revision INTEGER NOT NULL,char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,
            start_seconds REAL NOT NULL,end_seconds REAL NOT NULL,status TEXT NOT NULL,
            result TEXT,error TEXT,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT)''')
        connection.execute('CREATE INDEX IF NOT EXISTS alignment_job_text ON alignment_tasks(job_id,text_hash,revision,status)')


def _digest(text): return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _utf16len(text): return len(text.encode('utf-16-le')) // 2


def _utf16slice(text, start, end):
    try: return text.encode('utf-16-le')[start*2:end*2].decode('utf-16-le')
    except UnicodeError as exc: raise ValueError('Selection must end on complete characters') from exc


def _saved_review(job_id):
    saved = db.get_correction(job_id)
    if saved: return saved['text'], saved['revision']
    from transcript import readable_text
    path = db.DATA / 'results' / job_id / 'transcript.txt'
    if not path.is_file(): raise ValueError('Original transcript is unavailable')
    return readable_text(path.read_text(encoding='utf-8')), 0


def _public(row):
    if row is None: return None
    result = dict(row); result.pop('text',None); result.pop('text_hash',None)
    if result.get('result'): result['result'] = json.loads(result['result'])
    return result


@router.post('/api/jobs/{job_id}/correction/realign', status_code=202)
def queue_alignment(job_id: str, values: dict):
    job = db.get_job(job_id)
    if not job or job['status'] != 'done': raise HTTPException(409,'Choose a completed recording')
    text, revision = values.get('text'), values.get('revision')
    if not isinstance(text,str) or not text.strip() or len(text)>2_000_000 or isinstance(revision,bool) or not isinstance(revision,int):
        raise HTTPException(400,'Saved transcript text and its revision are required')
    try:
        saved, current_revision = _saved_review(job_id)
        if saved != text or revision != current_revision: raise ValueError('Save the latest transcript before realigning it')
        char_start, char_end = values.get('char_start',0), values.get('char_end',_utf16len(text))
        if any(isinstance(n,bool) or not isinstance(n,int) for n in (char_start,char_end)) or not 0<=char_start<char_end<=_utf16len(text):
            raise ValueError('Choose a valid transcript selection')
        _utf16slice(text,char_start,char_end)
        start, end = values.get('start_seconds',0), values.get('end_seconds',job.get('duration_s'))
        if any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in (start,end)) or not 0<=start<end<=float(job.get('duration_s') or 0)+.1:
            raise ValueError('Choose a valid audio interval')
        if end-start>3600: raise ValueError('Realign a passage of up to 60 minutes; select a shorter region for long recordings')
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    with db.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        pending = connection.execute("SELECT * FROM alignment_tasks WHERE job_id=? AND status IN ('queued','processing')",(job_id,)).fetchone()
        if pending:
            if pending['text_hash']==_digest(text) and pending['char_start']==char_start and pending['char_end']==char_end:return _public(pending)
            raise HTTPException(409,'An alignment is already queued for this recording')
        id=uuid.uuid4().hex
        connection.execute('''INSERT INTO alignment_tasks(id,job_id,text,text_hash,revision,char_start,char_end,start_seconds,end_seconds,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,'queued',?)''',(id,job_id,text,_digest(text),revision,char_start,char_end,start,end,db.now()))
        return _public(connection.execute('SELECT * FROM alignment_tasks WHERE id=?',(id,)).fetchone())


@router.get('/api/alignments/{task_id}')
def task_status(task_id: str):
    with db.connection() as connection: row=connection.execute('SELECT * FROM alignment_tasks WHERE id=?',(task_id,)).fetchone()
    if not row: raise HTTPException(404,'Alignment task not found')
    return _public(row)


@router.get('/api/jobs/{job_id}/correction/realign/latest')
def latest_task(job_id: str):
    with db.connection() as connection: row=connection.execute('SELECT * FROM alignment_tasks WHERE job_id=? ORDER BY created_at DESC LIMIT 1',(job_id,)).fetchone()
    return _public(row)


def recover_tasks():
    with db.connection() as connection:
        connection.execute("UPDATE alignment_tasks SET status='queued',error=NULL WHERE status='processing'")


def claim_task():
    with db.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute("SELECT * FROM alignment_tasks WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:return None
        connection.execute("UPDATE alignment_tasks SET status='processing',started_at=? WHERE id=?",(db.now(),row['id']))
        return dict(row)


def _finish(id,status,result=None,error=None):
    with db.connection() as connection:
        connection.execute('UPDATE alignment_tasks SET status=?,result=?,error=?,finished_at=? WHERE id=?',
                           (status,json.dumps(result,ensure_ascii=False) if result else None,error,db.now(),id))


def run_task(task, base_url):
    try:
        saved,revision=_saved_review(task['job_id'])
        if saved != task['text'] or revision != task['revision']:raise ValueError('A newer transcript revision superseded this alignment')
        with db.connection() as connection:
            row=connection.execute('SELECT input_path,status FROM jobs WHERE id=?',(task['job_id'],)).fetchone()
        if not row or row['status']!='done':raise ValueError('The recording is no longer available for alignment')
        source=Path(row['input_path'])
        if source.parent.resolve()!=(db.DATA/'uploads').resolve() or not source.is_file():raise ValueError('Original media is missing')
        directory=db.DATA/'tmp';directory.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='alignment-',dir=directory) as temp:
            wav=Path(temp)/'passage.wav'
            result=subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-protocol_whitelist','file,pipe','-ss',str(task['start_seconds']),'-i',str(source),'-t',str(task['end_seconds']-task['start_seconds']),'-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],capture_output=True,timeout=3600)
            if result.returncode:raise RuntimeError('Unable to decode the selected audio for alignment')
            selected=_utf16slice(task['text'],task['char_start'],task['char_end'])
            with wav.open('rb') as audio, httpx.Client(timeout=httpx.Timeout(connect=15,read=3600,write=180,pool=15)) as client:
                response=client.post(base_url+'/v1/audio/alignments',data={'text':selected},files={'file':('passage.wav',audio,'audio/wav')})
                if response.status_code==404:raise RuntimeError('Runtime forced alignment is unavailable. Rebuild and restart the model service.')
                response.raise_for_status();result=response.json()
            words=result.get('words')
            if not isinstance(words,list) or not words:raise RuntimeError('The CTC model could not align this selection')
            span=task['end_seconds']-task['start_seconds']
            for word in words:
                if (not isinstance(word,dict) or not isinstance(word.get('word'),str)
                    or any(isinstance(word.get(k),bool) or not isinstance(word.get(k),(int,float)) or not math.isfinite(word[k]) for k in ('start','end'))
                    or not 0<=word['start']<word['end']<=span+.1):raise RuntimeError('The model returned invalid alignment times')
                word['start']+=task['start_seconds'];word['end']+=task['start_seconds']
            original=db.DATA/'results'/task['job_id']/'transcript.json'
            if original.is_file():
                metadata=json.loads(original.read_text())
                turns=metadata.get('speaker_turns') or []
                if metadata.get('speaker_scope')=='file' and turns:
                    from diarization import assign_speakers
                    assign_speakers([{'start_seconds':0,'words':words}],turns)
            mapped=align_review(selected,words)
            if mapped['matched_words']!=mapped['total_words']:raise RuntimeError('Only part of the selected text could be aligned. Shorten the selection and check its wording.')
            for word in mapped['words']:word['char_start']+=task['char_start'];word['char_end']+=task['char_start']
            mapped['timestamp_source']='forced_ctc_alignment'
            saved,revision=_saved_review(task['job_id'])
            if saved!=task['text'] or revision!=task['revision']:raise ValueError('A newer transcript revision superseded this alignment')
            _finish(task['id'],'done',mapped)
    except Exception as exc:
        message=str(exc)
        if isinstance(exc,httpx.HTTPStatusError):
            try:message=exc.response.json().get('error',{}).get('message',message)
            except (ValueError,AttributeError):pass
        _finish(task['id'],'failed',error=message[:1200])


def current_alignment(job_id,text,revision):
    with db.connection() as connection:
        rows=connection.execute("SELECT * FROM alignment_tasks WHERE job_id=? AND text_hash=? AND revision=? AND status='done' ORDER BY created_at",(job_id,_digest(text),revision)).fetchall()
    if not rows:return None
    path=db.DATA/'results'/job_id/'transcript.json'
    words=[]
    if path.is_file():
        stat=path.stat();words=align_review(text,source_words(str(path),stat.st_mtime_ns,stat.st_size))['words']
    for row in rows:
        words=[w for w in words if w['char_end']<=row['char_start'] or w['char_start']>=row['char_end']]
        words.extend(json.loads(row['result'])['words'])
    words.sort(key=lambda w:w['char_start'])
    from review import TOKEN
    total=len(list(TOKEN.finditer(text)))
    return {'words':words,'matched_words':len(words),'total_words':total,'unmatched_words':max(0,total-len(words)),'timestamp_source':'forced_ctc_alignment'}
