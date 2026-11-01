"""HTTP services for the library, run comparison, reference evaluation and resumable uploads."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from fastapi import APIRouter, HTTPException, Request

import db
from media import probe_audio
import workspace_store as store

router=APIRouter()
CHUNK_LIMIT=16*1024*1024


def roots():
    import main
    return main.INPUT, main.OUTPUT, main.MAX_UPLOAD, main.MIN_FREE


def translated(operation,*args,**kwargs):
    try:return operation(*args,**kwargs)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    except ValueError as error:raise HTTPException(400,str(error)) from error


@router.get('/api/workspace')
def workspace():
    return {'profiles':store.list_profiles(),'vocabulary':store.list_vocabulary(),
            'projects':store.list_projects(),'queue':store.queue_estimate()}


@router.get('/api/profiles')
def profiles():return {'profiles':store.list_profiles()}


@router.post('/api/profiles',status_code=201)
def add_profile(values:dict):return translated(store.save_profile,values)


@router.put('/api/profiles/{id}')
def edit_profile(id:str,values:dict):return translated(store.save_profile,{**values,'_existing':True},id)


@router.delete('/api/profiles/{id}',status_code=204)
def delete_profile(id:str):
    with db.connection() as connection:
        if not connection.execute('DELETE FROM profiles WHERE id=?',(id,)).rowcount:raise HTTPException(404,'Profile not found')


@router.post('/api/profiles/{id}/apply')
def apply_profile(id:str):
    profile=next((profile for profile in store.list_profiles() if profile['id']==id),None)
    if not profile:raise HTTPException(404,'Profile not found')
    # Applying a transcription profile must not unexpectedly pause/resume the queue.
    settings={key:value for key,value in profile['settings'].items() if key in db.DEFAULTS and key!='queue_paused'}
    return {'settings':translated(db.save_settings,settings)}


@router.get('/api/vocabulary')
def vocabulary():return {'vocabulary':store.list_vocabulary()}


@router.post('/api/vocabulary',status_code=201)
def add_vocabulary(values:dict):return translated(store.save_vocabulary,values)


@router.put('/api/vocabulary/{id}')
def edit_vocabulary(id:str,values:dict):return translated(store.save_vocabulary,{**values,'_existing':True},id)


@router.delete('/api/vocabulary/{id}',status_code=204)
def delete_vocabulary(id:str):
    if id in db.get_settings().get('vocabulary_ids',[]) or any(id in p['settings'].get('vocabulary_ids',[]) for p in store.list_profiles()):
        raise HTTPException(409,'This vocabulary is selected in defaults or a saved profile. Unselect it before deleting.')
    with db.connection() as connection:
        if not connection.execute('DELETE FROM vocabulary WHERE id=?',(id,)).rowcount:raise HTTPException(404,'Vocabulary not found')


@router.get('/api/projects')
def projects():return {'projects':store.list_projects()}


@router.post('/api/projects',status_code=201)
def add_project(values:dict):return translated(store.save_project,values)


@router.delete('/api/projects/{id}',status_code=204)
def delete_project(id:str):
    with db.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not connection.execute('DELETE FROM projects WHERE id=?',(id,)).rowcount:raise HTTPException(404,'Project not found')
        connection.execute("UPDATE jobs SET project='' WHERE project=?",(id,))


@router.patch('/api/jobs/{id}/metadata')
def metadata(id:str,values:dict):return translated(store.update_metadata,id,values)


@router.post('/api/jobs/{id}/control')
def control(id:str,values:dict):return translated(store.control_job,id,values.get('action'))


@router.post('/api/jobs/bulk')
def bulk(values:dict):
    ids=values.get('ids');action=values.get('action')
    allowed={'retry','retry_current','pause','resume','cancel','archive','unarchive','delete','metadata'}
    if not isinstance(ids,list) or not 1<=len(ids)<=200 or any(not isinstance(id,str) or len(id)>80 for id in ids):
        raise HTTPException(400,'Select 1–200 recordings')
    if action not in allowed:raise HTTPException(400,'Unsupported bulk action')
    result=[]
    import main
    for id in dict.fromkeys(ids):
        try:
            if action in {'pause','resume','cancel'}:store.control_job(id,action)
            elif action in {'archive','unarchive'}:store.update_metadata(id,{'archived':action=='archive'})
            elif action=='metadata':store.update_metadata(id,{key:values[key] for key in ('project','tags','archived') if key in values})
            elif action=='delete':
                main.delete(id)
                cleanup_job(id)
            else:
                if not db.get_job(id):raise LookupError('Recording not found')
                store.archive_run(id,roots()[1])
                db.retry_job(id,action=='retry_current')
            result.append({'id':id,'ok':True})
        except (ValueError,LookupError,HTTPException,OSError) as error:
            result.append({'id':id,'ok':False,'error':str(error.detail) if isinstance(error,HTTPException) else str(error)})
    return {'results':result,'succeeded':sum(item['ok'] for item in result),'failed':sum(not item['ok'] for item in result)}


def cleanup_job(id):
    """Integrators should also call this from ordinary job deletion."""
    with db.connection() as connection:
        sessions=connection.execute('SELECT id FROM upload_sessions WHERE job_id=? OR id=?',(id,id)).fetchall()
        connection.execute('DELETE FROM runs WHERE job_id=?',(id,))
        connection.execute('DELETE FROM benchmarks WHERE job_id=?',(id,))
        connection.execute('DELETE FROM upload_sessions WHERE job_id=? OR id=?',(id,id))
    for session in sessions:
        directory=session_directory();identifier=session['id']
        for suffix in ('.part','.lock'):(directory/(identifier+suffix)).unlink(missing_ok=True)
        for temporary in directory.glob(identifier+'.chunk-*'):temporary.unlink(missing_ok=True)


@router.get('/api/jobs/{id}/runs')
def runs(id:str):return {'runs':translated(store.list_runs,id,roots()[1]),'current':'current'}


@router.get('/api/jobs/{id}/compare')
def compare(id:str,left:str='current',right:str='current'):
    return translated(store.compare_runs,id,left,right,roots()[1])


@router.get('/api/benchmarks')
def benchmarks():return {'benchmarks':store.list_benchmarks()}


@router.post('/api/benchmarks',status_code=201)
def benchmark(values:dict):return translated(store.score_benchmark,values,roots()[1])


@router.delete('/api/benchmarks/{id}',status_code=204)
def delete_benchmark(id:str):
    with db.connection() as connection:
        if not connection.execute('DELETE FROM benchmarks WHERE id=?',(id,)).rowcount:raise HTTPException(404,'Benchmark not found')


@router.post('/api/jobs/{id}/variant',status_code=201)
def variant(id:str,values:dict):
    with db.connection() as connection:
        original=connection.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
    if not original:raise HTTPException(404,'Recording not found')
    original=dict(original)
    source=Path(original['input_path'])
    input_dir,output_dir,limit,min_free=roots()
    if source.parent.resolve()!=input_dir.resolve() or not source.is_file():raise HTTPException(404,'Original recording is unavailable')
    base={**db.get_settings(),'input_language':original['language'],
          **{key:original[key] for key in ('output_language','segment_seconds','diarize','number_style','guidance','temperature','seed')}}
    base['diarize']=bool(base['diarize'])
    base['vocabulary_ids']=[]
    options=json.loads(original.get('processing_options') or '{}')
    base.update({key:options[key] for key in ('chunk_mode','overlap_seconds','channel_mode') if key in options})
    settings=translated(store.validate_settings,values.get('settings',{}),base)
    if settings['diarize'] and (original.get('duration_s') or 0)>3600:
        raise HTTPException(400,'File-wide speaker identification supports recordings up to 60 minutes; disable speakers for longer recordings')
    identifier=uuid.uuid4().hex;destination=input_dir/(identifier+source.suffix)
    try:
        try:os.link(source,destination)
        except OSError:
            if shutil.disk_usage(input_dir).free<source.stat().st_size+min_free:raise HTTPException(507,'Insufficient disk space for another run')
            shutil.copyfile(source,destination)
        db.create_job(identifier,original['filename'],str(destination),settings['input_language'],settings['segment_seconds'],settings['diarize'],
                      original.get('duration_s'),settings['output_language'],settings['number_style'],store.guidance_for(settings),settings['temperature'],settings['seed'],processing_options=store.snapshot_options(settings))
        group=original.get('group_id') or id
        with db.connection() as connection:
            connection.execute('UPDATE jobs SET group_id=? WHERE id=?',(group,id))
            connection.execute('UPDATE jobs SET group_id=?,processing_options=?,project=?,tags=? WHERE id=?',
                               (group,json.dumps(store.snapshot_options(settings),ensure_ascii=False),original['project'],original['tags'],identifier))
        return {'id':identifier,'group_id':group,'status':'queued'}
    except ValueError as error:
        if not db.get_job(identifier):destination.unlink(missing_ok=True)
        raise HTTPException(429 if 'Queue is full' in str(error) else 400,str(error)) from error
    except OSError as error:
        if not db.get_job(identifier):destination.unlink(missing_ok=True)
        raise HTTPException(507,'Unable to create another run; check recording availability and free storage') from error
    except BaseException:
        # Once the job is created its source belongs to the durable worker queue.
        if not db.get_job(identifier):destination.unlink(missing_ok=True)
        raise


def session_directory():
    directory=db.DATA/'incoming';directory.mkdir(parents=True,exist_ok=True)
    return directory


def session_id(id):
    if not re.fullmatch(r'[a-f0-9]{32}',id):raise HTTPException(404,'Upload session not found')
    return id


@contextmanager
def session_lock(id):
    read_session(id)
    with (session_directory()/(id+'.lock')).open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise HTTPException(409,'Another request is updating this upload; retry shortly') from error
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def read_session(id):
    session_id(id)
    with db.connection() as connection:
        row=connection.execute('SELECT * FROM upload_sessions WHERE id=?',(id,)).fetchone()
    if not row:raise HTTPException(404,'Upload session not found')
    return dict(row)


def public_session(row):
    return {key:row.get(key) for key in ('id','filename','size','offset','status','job_id','error','created_at','updated_at')}|{'chunk_size':CHUNK_LIMIT}


def expire_sessions():
    cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    with db.connection() as connection:
        rows=connection.execute("SELECT id,filename FROM upload_sessions WHERE updated_at<? AND status!='complete'",(cutoff,)).fetchall()
    for row in rows:
        try:
            with session_lock(row['id']):
                (session_directory()/(row['id']+'.part')).unlink(missing_ok=True)
                if not db.get_job(row['id']):
                    (roots()[0]/(row['id']+Path(row['filename']).suffix.lower())).unlink(missing_ok=True)
                with db.connection() as connection:connection.execute('DELETE FROM upload_sessions WHERE id=? AND updated_at<?',(row['id'],cutoff))
        except HTTPException:pass


@router.post('/api/uploads',status_code=201)
def start_upload(values:dict):
    import main
    filename=Path(str(values.get('filename','')).replace('\\','/')).name[:180]
    size=values.get('size')
    if not filename or Path(filename).suffix.lower() not in main.EXTS:raise HTTPException(400,'Unsupported audio or video extension')
    if isinstance(size,bool) or not isinstance(size,int) or not 0<size<=main.MAX_UPLOAD:raise HTTPException(413,'Upload size exceeds the configured limit or is empty')
    settings=translated(store.validate_settings,values.get('settings',{}),db.get_settings())
    settings={**settings,'resolved_guidance':store.guidance_for(settings),'processing_options_snapshot':store.snapshot_options(settings)}
    metadata=translated(store.validate_metadata,{key:values[key] for key in ('project','tags') if key in values})
    fingerprint=values.get('fingerprint')
    if fingerprint is not None and (not isinstance(fingerprint,str) or len(fingerprint)>300):raise HTTPException(400,'Invalid file fingerprint')
    expire_sessions()
    with db.connection() as connection:
        if fingerprint:
            row=connection.execute("SELECT * FROM upload_sessions WHERE fingerprint=? AND filename=? AND size=? AND status IN ('uploading','ready') ORDER BY created_at DESC LIMIT 1",(fingerprint,filename,size)).fetchone()
            if row and json.loads(row['settings'])==settings and json.loads(row['metadata'])==metadata:
                return public_session(dict(row))
        if connection.execute("SELECT count(*) FROM upload_sessions WHERE status NOT IN ('complete','failed')").fetchone()[0]>=100:
            raise HTTPException(429,'Too many unfinished uploads; finish or discard existing sessions')
    if shutil.disk_usage(main.INPUT).free<size+main.MIN_FREE:raise HTTPException(507,'Insufficient free disk space for this upload')
    id=uuid.uuid4().hex;stamp=db.now()
    path=session_directory()/(id+'.part');path.touch(exist_ok=False)
    try:
        with db.connection() as connection:
            connection.execute('INSERT INTO upload_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (id,filename,size,0,json.dumps(settings,ensure_ascii=False),json.dumps(metadata),fingerprint,'uploading',None,None,stamp,stamp))
    except BaseException:path.unlink(missing_ok=True);raise
    return public_session(read_session(id))


@router.get('/api/uploads/{id}')
def upload_status(id:str):return public_session(read_session(id))


@router.patch('/api/uploads/{id}')
async def upload_chunk(id:str,request:Request,offset:int):
    # Receive a bounded chunk before locking; slow clients do not hold a session lock.
    row=read_session(id)
    if row['status'] not in {'uploading','ready'}:raise HTTPException(409,'This upload is no longer accepting data')
    if offset<0 or offset>row['size']:raise HTTPException(400,'Invalid upload offset')
    declared=request.headers.get('content-length')
    if declared:
        try:declared=int(declared)
        except ValueError as error:raise HTTPException(400,'Invalid content length') from error
        if not 0<declared<=CHUNK_LIMIT:raise HTTPException(413,'Upload chunks must contain 1–16 MiB')
    directory=session_directory();temporary=directory/(id+'.chunk-'+uuid.uuid4().hex);size=0
    try:
        with temporary.open('xb') as chunk:
            async for block in request.stream():
                size+=len(block)
                if size>CHUNK_LIMIT or offset+size>row['size']:raise HTTPException(413,'Chunk exceeds the upload boundary')
                if shutil.disk_usage(directory).free<len(block)+roots()[3]:raise HTTPException(507,'Insufficient disk space while uploading')
                await asyncio.to_thread(chunk.write,block)
            chunk.flush();os.fsync(chunk.fileno())
        if not size:raise HTTPException(400,'Empty upload chunk')
        checksum=request.headers.get('upload-checksum')
        if checksum:
            if not re.fullmatch(r'[a-fA-F0-9]{64}',checksum) or hashlib.sha256(temporary.read_bytes()).hexdigest()!=checksum.lower():
                raise HTTPException(400,'Chunk checksum mismatch')
        with session_lock(id):
            row=read_session(id)
            if row['status'] not in {'uploading','ready'}:raise HTTPException(409,'This upload is no longer accepting data')
            if row['offset']!=offset:raise HTTPException(409,f'Upload offset mismatch; resume at {row["offset"]}')
            part=directory/(id+'.part')
            with part.open('r+b') as destination,temporary.open('rb') as source:
                actual=destination.seek(0,os.SEEK_END)
                if actual<offset:raise HTTPException(409,'Uploaded data is incomplete; discard this session and select the file again')
                destination.truncate(offset);destination.seek(offset)
                shutil.copyfileobj(source,destination,1024*1024)
                destination.flush();os.fsync(destination.fileno())
            next_offset=offset+size
            with db.connection() as connection:
                connection.execute('UPDATE upload_sessions SET offset=?,status=?,updated_at=? WHERE id=?',
                                   (next_offset,'ready' if next_offset==row['size'] else 'uploading',db.now(),id))
            return public_session(read_session(id))
    except OSError as error:raise HTTPException(507,'Unable to save the upload chunk; check storage and retry') from error
    finally:temporary.unlink(missing_ok=True)


@router.post('/api/uploads/{id}/complete',status_code=201)
def complete_upload(id:str):
    import main
    with session_lock(id):
        row=read_session(id)
        if row['status']=='complete':
            if not db.get_job(row['job_id']):raise HTTPException(410,'This uploaded recording was deleted; start a new upload')
            return {'id':row['job_id'],'status':db.get_job(row['job_id'])['status']}
        if row['offset']!=row['size']:raise HTTPException(409,'Upload is not complete; resume from its saved offset')
        if row['status']=='failed':raise HTTPException(400,row['error'] or 'This recording failed validation; discard the upload and try another file')
        destination=main.INPUT/(id+Path(row['filename']).suffix.lower())
        part=session_directory()/(id+'.part')
        if not db.get_job(id):
            if not destination.exists():
                if not part.is_file() or part.stat().st_size!=row['size']:raise HTTPException(409,'The saved upload is incomplete')
                part.replace(destination)
            settings=json.loads(row['settings']);metadata=json.loads(row['metadata'])
            try:
                duration=probe_audio(destination)
                if settings['diarize'] and duration>3600:raise ValueError('File-wide speaker identification supports recordings up to 60 minutes; disable speakers for longer recordings')
                db.create_job(id,row['filename'],str(destination),settings['input_language'],settings['segment_seconds'],settings['diarize'],duration,
                              settings['output_language'],settings['number_style'],settings.get('resolved_guidance',settings['guidance']),settings['temperature'],settings['seed'],processing_options=settings.get('processing_options_snapshot') or store.snapshot_options(settings))
            except ValueError as error:
                # Queue pressure is recoverable: preserve the already validated source for retry.
                if 'Queue is full' in str(error):raise HTTPException(429,str(error)) from error
                with db.connection() as connection:connection.execute("UPDATE upload_sessions SET status='failed',error=?,updated_at=? WHERE id=?",(str(error),db.now(),id))
                raise HTTPException(400,str(error)) from error
        else:
            settings=json.loads(row['settings']);metadata=json.loads(row['metadata'])
        with db.connection() as connection:
            connection.execute('UPDATE jobs SET processing_options=?,project=?,tags=? WHERE id=?',
                (json.dumps(settings.get('processing_options_snapshot') or store.snapshot_options(settings),ensure_ascii=False),metadata.get('project',''),metadata.get('tags','[]'),id))
            connection.execute("UPDATE upload_sessions SET status='complete',job_id=?,error=NULL,updated_at=? WHERE id=?",(id,db.now(),id))
        return {'id':id,'status':db.get_job(id)['status']}


@router.delete('/api/uploads/{id}',status_code=204)
def discard_upload(id:str):
    with session_lock(id):
        row=read_session(id)
        if row['status']=='complete':raise HTTPException(409,'This upload is already a recording; delete it from Files')
        (session_directory()/(id+'.part')).unlink(missing_ok=True)
        if not db.get_job(id):
            input_dir,_,_,_=roots()
            (input_dir/(id+Path(row['filename']).suffix.lower())).unlink(missing_ok=True)
        with db.connection() as connection:connection.execute('DELETE FROM upload_sessions WHERE id=?',(id,))
