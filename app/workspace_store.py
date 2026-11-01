"""Persistent library, run history, settings profiles and reference-based evaluation.

Paths are resolved from db.DATA at call time so isolated tests and data migrations
never accidentally read or write the running deployment.
"""
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import unicodedata
import uuid
import zlib

import db
from settings import CAPACITY, DEFAULTS, LANGUAGES

ACTIVE = {'preparing', 'processing'}
JOB_COLUMNS = {
    'processing_options': "TEXT NOT NULL DEFAULT '{}'",
    'project': "TEXT NOT NULL DEFAULT ''",
    'tags': "TEXT NOT NULL DEFAULT '[]'",
    'archived': 'INTEGER NOT NULL DEFAULT 0',
    'control_action': 'TEXT',
    'group_id': 'TEXT',
}


def init_schema():
    """Called after db.init_db(), never recursively invokes the base migration."""
    with db.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        existing = {row['name'] for row in connection.execute('PRAGMA table_info(jobs)')}
        for column, definition in JOB_COLUMNS.items():
            if column not in existing:
                connection.execute(f'ALTER TABLE jobs ADD COLUMN {column} {definition}')
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS profiles (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,settings TEXT NOT NULL,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vocabulary (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,terms TEXT NOT NULL,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY,job_id TEXT NOT NULL,digest TEXT NOT NULL,
              filename TEXT NOT NULL,text TEXT NOT NULL,payload BLOB NOT NULL,
              metrics TEXT NOT NULL,settings TEXT NOT NULL,created_at TEXT NOT NULL,
              UNIQUE(job_id,digest));
            CREATE INDEX IF NOT EXISTS runs_job_created ON runs(job_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS benchmarks (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,job_id TEXT NOT NULL,run_id TEXT NOT NULL,
              reference TEXT NOT NULL,reference_source TEXT NOT NULL,hypothesis TEXT NOT NULL,
              report TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS upload_sessions (
              id TEXT PRIMARY KEY,filename TEXT NOT NULL,size INTEGER NOT NULL,
              offset INTEGER NOT NULL DEFAULT 0,settings TEXT NOT NULL,metadata TEXT NOT NULL,
              fingerprint TEXT,status TEXT NOT NULL DEFAULT 'uploading',job_id TEXT,error TEXT,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS uploads_updated ON upload_sessions(updated_at);
            CREATE INDEX IF NOT EXISTS jobs_project_archive ON jobs(project,archived,created_at);
        ''')


def short_text(value, label, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit or '\x00' in value:
        raise ValueError(f'{label} must contain 1–{limit} characters')
    return value.strip()


def validate_settings(values, base=None):
    """Validate snapshots without changing global defaults."""
    if not isinstance(values, dict):
        raise ValueError('Settings must be an object')
    aliases = {'language': 'input_language'}
    values = {aliases.get(key, key): value for key, value in values.items()}
    allowed = set(DEFAULTS) | {'chunk_mode', 'overlap_seconds', 'channel_mode', 'vocabulary_ids'}
    if not values.keys() <= allowed:
        raise ValueError('Unknown transcription settings')
    result = {**DEFAULTS, 'chunk_mode':'fixed', 'overlap_seconds':0, 'channel_mode':'mix', 'vocabulary_ids':[], **(base or {}), **values}
    if isinstance(result['parallel_jobs'], bool) or not isinstance(result['parallel_jobs'], int) or not 1 <= result['parallel_jobs'] <= CAPACITY:
        raise ValueError(f'Parallel files must be between 1 and {CAPACITY}')
    if result['input_language'] not in {*LANGUAGES, 'auto'} or result['output_language'] not in {*LANGUAGES, 'source'}:
        raise ValueError('Unsupported input or output language')
    if isinstance(result['segment_seconds'], bool) or result['segment_seconds'] not in {60,120,300}:
        raise ValueError('Segment length must be 60, 120 or 300 seconds')
    if not isinstance(result['diarize'], bool) or not isinstance(result['queue_paused'], bool):
        raise ValueError('Speaker detection and queue pause must be booleans')
    if result['diarize'] and result['output_language'] not in {'source',result['input_language']}:
        raise ValueError('Speaker detection requires original-language output')
    if result['number_style'] not in {'digits','natural'}:
        raise ValueError('Unsupported number formatting')
    if not isinstance(result['guidance'], str) or len(result['guidance']) > 4000 or any(s in result['guidance'] for s in ('<|','|>','\x00')):
        raise ValueError('Guidance must contain at most 4000 characters and no control tokens')
    if isinstance(result['temperature'], bool) or not isinstance(result['temperature'], (int,float)) or not math.isfinite(result['temperature']) or not 0 <= result['temperature'] <= 1:
        raise ValueError('Temperature must be between 0 and 1')
    if result['seed'] is not None and (isinstance(result['seed'], bool) or not isinstance(result['seed'], int) or not 0 <= result['seed'] <= 2147483647):
        raise ValueError('Seed must be empty or an integer from 0 to 2147483647')
    if result['chunk_mode'] not in {'fixed','silence'} or result['channel_mode'] not in {'mix','separate'}:
        raise ValueError('Unsupported chunk or channel mode')
    overlap = result['overlap_seconds']
    if isinstance(overlap,bool) or not isinstance(overlap,(int,float)) or not math.isfinite(overlap) or not 0 <= overlap <= 10:
        raise ValueError('Overlap must be between 0 and 10 seconds')
    ids = result['vocabulary_ids']
    if not isinstance(ids,list) or len(ids)>20 or any(not isinstance(value,str) or len(value)>80 for value in ids):
        raise ValueError('Select at most 20 vocabulary lists')
    result['vocabulary_ids'] = list(dict.fromkeys(ids))
    # Resolve selected IDs now: deleted or unknown vocabularies cannot silently alter a run.
    vocabulary_terms(result['vocabulary_ids'])
    guidance_for(result)
    return result


def vocabulary_terms(ids):
    terms=[]
    with db.connection() as connection:
        for id in ids:
            row=connection.execute('SELECT terms FROM vocabulary WHERE id=?',(id,)).fetchone()
            if row is None:
                raise ValueError('A selected vocabulary no longer exists')
            terms.extend(json.loads(row['terms']))
    return list(dict.fromkeys(terms))


def snapshot_options(settings):
    return {'chunk_mode':settings.get('chunk_mode','fixed'),
            'overlap_seconds':settings.get('overlap_seconds',0),
            'channel_mode':settings.get('channel_mode','mix'),
            'vocabulary':vocabulary_terms(settings.get('vocabulary_ids',[]))}


def guidance_for(settings):
    guidance=settings.get('guidance','').strip()
    terms=vocabulary_terms(settings.get('vocabulary_ids',[]))
    if terms:
        guidance += ('\n' if guidance else '') + 'Context vocabulary: ' + ', '.join(terms) + '. Use these spellings only when supported by the recording.'
    if len(guidance)>4000:
        raise ValueError('Combined guidance and vocabulary exceed 4000 characters; select fewer terms')
    return guidance


def list_profiles():
    with db.connection() as connection:
        rows=connection.execute('SELECT * FROM profiles ORDER BY name COLLATE NOCASE,id').fetchall()
    return [{**dict(row),'settings':json.loads(row['settings'])} for row in rows]


def save_profile(values,id=None):
    name=short_text(values.get('name'),'Profile name')
    # Store a complete validated snapshot; using a profile never depends on later defaults.
    settings=validate_settings(values.get('settings',{}),db.get_settings())
    id=id or uuid.uuid4().hex
    stamp=db.now()
    with db.connection() as connection:
        if id and values.get('_existing') and not connection.execute('SELECT 1 FROM profiles WHERE id=?',(id,)).fetchone():
            raise LookupError('Profile not found')
        connection.execute('''INSERT INTO profiles VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,settings=excluded.settings,updated_at=excluded.updated_at''',
            (id,name,json.dumps(settings,ensure_ascii=False),stamp,stamp))
    return next(profile for profile in list_profiles() if profile['id']==id)


def list_vocabulary():
    with db.connection() as connection:
        rows=connection.execute('SELECT * FROM vocabulary ORDER BY name COLLATE NOCASE,id').fetchall()
    return [{**dict(row),'terms':json.loads(row['terms'])} for row in rows]


def save_vocabulary(values,id=None):
    name=short_text(values.get('name'),'Vocabulary name')
    terms=values.get('terms')
    if isinstance(terms,str):
        terms=terms.splitlines()
    if not isinstance(terms,list) or not 1<=len(terms)<=1000:
        raise ValueError('Vocabulary must contain 1–1000 terms')
    clean=[]
    for term in terms:
        term=short_text(term,'Vocabulary term',100)
        if any(token in term for token in ('<|','|>')):
            raise ValueError('Vocabulary terms cannot contain model control tokens')
        clean.append(term)
    clean=list(dict.fromkeys(clean))
    id=id or uuid.uuid4().hex;stamp=db.now()
    with db.connection() as connection:
        if values.get('_existing') and not connection.execute('SELECT 1 FROM vocabulary WHERE id=?',(id,)).fetchone():
            raise LookupError('Vocabulary not found')
        connection.execute('''INSERT INTO vocabulary VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,terms=excluded.terms,updated_at=excluded.updated_at''',
            (id,name,json.dumps(clean,ensure_ascii=False),stamp,stamp))
    return next(item for item in list_vocabulary() if item['id']==id)


def list_projects():
    with db.connection() as connection:
        return [dict(row) for row in connection.execute('''SELECT p.*,count(j.id) AS files FROM projects p
            LEFT JOIN jobs j ON j.project=p.id GROUP BY p.id ORDER BY p.name COLLATE NOCASE,p.id''')]


def save_project(values):
    name=short_text(values.get('name'),'Project name')
    id=uuid.uuid4().hex
    with db.connection() as connection:
        connection.execute('INSERT INTO projects VALUES(?,?,?)',(id,name,db.now()))
    return next(project for project in list_projects() if project['id']==id)


def validate_metadata(values):
    if not isinstance(values,dict) or not values.keys()<={'project','tags','archived'}:
        raise ValueError('Unknown recording metadata')
    result={}
    if 'project' in values:
        project=values['project']
        if not isinstance(project,str) or len(project)>80:
            raise ValueError('Invalid project')
        if project:
            with db.connection() as connection:
                if not connection.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone():
                    raise ValueError('Project not found')
        result['project']=project
    if 'tags' in values:
        tags=values['tags']
        if not isinstance(tags,list) or len(tags)>20:
            raise ValueError('Use at most 20 labels')
        result['tags']=json.dumps(list(dict.fromkeys(short_text(tag,'Label',40) for tag in tags)),ensure_ascii=False)
    if 'archived' in values:
        if not isinstance(values['archived'],bool):raise ValueError('Archived must be a boolean')
        result['archived']=int(values['archived'])
    return result


def update_metadata(id,values):
    updates=validate_metadata(values)
    with db.connection() as connection:
        if not connection.execute('SELECT 1 FROM jobs WHERE id=?',(id,)).fetchone():raise LookupError('Recording not found')
        if updates:
            connection.execute('UPDATE jobs SET '+','.join(f'{key}=?' for key in updates)+' WHERE id=?', [*updates.values(),id])
    return db.get_job(id)


def control_job(id,action):
    if action not in {'pause','resume','cancel'}:raise ValueError('Unsupported recording action')
    with db.connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
        if not row:raise LookupError('Recording not found')
        status=row['status']
        if action=='resume':
            if status not in {'paused','cancelled'}:raise ValueError('Only paused or cancelled recordings can be resumed')
            if not Path(row['input_path']).is_file():raise ValueError('Original recording is missing; upload it again')
            count=connection.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','preparing','processing')").fetchone()[0]
            if count>=int(os.getenv('MAX_PENDING_JOBS','100')):raise ValueError('Queue is full; wait before resuming')
            connection.execute("UPDATE jobs SET status='queued',stage='recovering',control_action=NULL,error=NULL,error_code=NULL,eta_seconds=NULL,finished_at=NULL,updated_at=? WHERE id=?",(db.now(),id))
        elif status in ACTIVE:
            connection.execute('UPDATE jobs SET control_action=?,eta_seconds=NULL,updated_at=? WHERE id=?',(action,db.now(),id))
        elif status in {'queued','paused'} or (action=='cancel' and status=='failed'):
            target='paused' if action=='pause' else 'cancelled'
            connection.execute('UPDATE jobs SET status=?,stage=?,control_action=NULL,eta_seconds=NULL,updated_at=? WHERE id=?',(target,target,db.now(),id))
        elif (action=='pause' and status=='paused') or (action=='cancel' and status=='cancelled'):
            pass
        else:raise ValueError('This recording cannot be '+('paused' if action=='pause' else 'cancelled'))
    return db.get_job(id)


def _result_directory(output_dir=None):
    return Path(output_dir) if output_dir is not None else db.DATA/'results'


def archive_run(job_id,output_dir=None):
    """Idempotently retain completed results before retry overwrites their files."""
    root=_result_directory(output_dir).resolve()
    directory=root/job_id
    if directory.resolve().parent!=root:raise ValueError('Invalid recording result directory')
    source=directory/'transcript.json'
    if not source.is_file():return None
    payload=source.read_bytes()
    try:document=json.loads(payload)
    except (ValueError,UnicodeError):
        import base64
        textfile=directory/'transcript.txt'
        document={'text':textfile.read_text(errors='replace') if textfile.is_file() else '',
                  'result_warning':'Original JSON was damaged; raw bytes retained for recovery',
                  'original_json_base64':base64.b64encode(payload).decode('ascii')}
    stored_payload=json.dumps(document,ensure_ascii=False).encode('utf-8')
    with db.connection() as connection:
        row=connection.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:return None
    job=dict(row)
    # JSON metadata survives retry resetting the job row's process-time fields.
    metrics={key:document.get(key,job.get(key)) for key in ('duration_s','wall_s','inference_s','rtfx','inference_rtfx','backend')}
    settings={key:job.get(key) for key in ('language','segment_seconds','diarize','output_language','number_style','guidance','temperature','seed','processing_options')}
    settings.update({key:document[key] for key in ('guidance','temperature','seed','number_style','output_language','processing_options') if key in document})
    if document.get('language_hint'):settings['language']=document['language_hint']
    digest=hashlib.sha256(payload).hexdigest();id=uuid.uuid4().hex
    with db.connection() as connection:
        connection.execute('''INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?,?,?)''',(id,job_id,digest,job['filename'],document.get('text',''),zlib.compress(stored_payload),json.dumps(metrics),json.dumps(settings),db.now()))
        return connection.execute('SELECT id FROM runs WHERE job_id=? AND digest=?',(job_id,digest)).fetchone()['id']


def _run_public(row):
    value=dict(row);value.pop('payload',None);value.pop('digest',None)
    for key in ('metrics','settings'):value[key]=json.loads(value[key])
    options=value['settings'].get('processing_options')
    if isinstance(options,str):
        try:value['settings']['processing_options']=json.loads(options)
        except ValueError:value['settings']['processing_options']={}
    return value


def _group_jobs(job_id):
    with db.connection() as connection:
        row=connection.execute('SELECT COALESCE(group_id,id) AS family FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:raise LookupError('Recording not found')
        return [item['id'] for item in connection.execute('SELECT id FROM jobs WHERE COALESCE(group_id,id)=? ORDER BY created_at DESC',(row['family'],))]


def read_run(job_id,run_id='current',output_dir=None):
    family=_group_jobs(job_id)
    if run_id=='current' or (isinstance(run_id,str) and run_id.startswith('current:')):
        target=job_id if run_id=='current' else run_id.split(':',1)[1]
        if target not in family:raise LookupError('Run does not belong to this recording family')
        job=db.get_job(target)
        source=_result_directory(output_dir)/target/'transcript.json'
        if job['status']!='done' or not source.is_file():raise ValueError('Current completed transcript is unavailable')
        try:document=json.loads(source.read_text())
        except (OSError,ValueError) as error:raise ValueError('Current result is damaged; retry the recording to regenerate it') from error
        return {'id':run_id,'job_id':target,'filename':job['filename'],'text':document.get('text',''),
                'metrics':{key:document.get(key,job.get(key)) for key in ('duration_s','wall_s','inference_s','rtfx','inference_rtfx','backend')},
                'settings':{key:job.get(key) for key in ('language','segment_seconds','diarize','output_language','number_style','guidance','temperature','seed','processing_options')},
                'created_at':job.get('finished_at') or job['updated_at'],'document':document}
    placeholders=','.join('?' for _ in family)
    with db.connection() as connection:
        row=connection.execute('SELECT * FROM runs WHERE id=? AND job_id IN ('+placeholders+')',[run_id,*family]).fetchone()
    if row is None:raise LookupError('Retained run not found')
    return {**_run_public(row),'document':json.loads(zlib.decompress(row['payload']))}


def list_runs(job_id,output_dir=None):
    family=_group_jobs(job_id);placeholders=','.join('?' for _ in family)
    with db.connection() as connection:
        rows=connection.execute('SELECT * FROM runs WHERE job_id IN ('+placeholders+') ORDER BY created_at DESC LIMIT 100',family).fetchall()
    result=[_run_public(row) for row in rows]
    for target in reversed(family[:100]):
        try:
            current=read_run(job_id,'current' if target==job_id else 'current:'+target,output_dir)
            current.pop('document',None);result.insert(0,current)
        except ValueError:pass
    return result


def compare_runs(job_id,left='current',right='current',output_dir=None):
    a=read_run(job_id,left,output_dir);b=read_run(job_id,right,output_dir)
    # JSON text only, never markup: callers render tokens with textContent.
    tokens_a=re.findall(r'\S+\s*',a['text'][:200000]);tokens_b=re.findall(r'\S+\s*',b['text'][:200000])
    matcher=difflib.SequenceMatcher(a=tokens_a,b=tokens_b,autojunk=True)
    diff=[{'operation':operation,'left':''.join(tokens_a[i:j]),'right':''.join(tokens_b[k:l])}
          for operation,i,j,k,l in matcher.get_opcodes()]
    for item in (a,b):item.pop('document',None)
    return {'left':a,'right':b,'diff':diff,'truncated':len(a['text'])>200000 or len(b['text'])>200000,
            'metrics':{'wall_s_difference':(b['metrics'].get('wall_s') or 0)-(a['metrics'].get('wall_s') or 0)}}


def normalized_text(text):
    return ' '.join(''.join(c if c.isalnum() or c.isspace() else ' ' for c in unicodedata.normalize('NFC',text).casefold()).split())


def edit_counts(reference,hypothesis,max_cells=25_000_000):
    """Exact Levenshtein counts with linear memory and a bounded compute budget."""
    prefix=0
    while prefix<min(len(reference),len(hypothesis)) and reference[prefix]==hypothesis[prefix]:prefix+=1
    reference_tail=reference[prefix:];hypothesis_tail=hypothesis[prefix:]
    suffix=0
    while suffix<min(len(reference_tail),len(hypothesis_tail)) and reference_tail[-suffix-1]==hypothesis_tail[-suffix-1]:suffix+=1
    a=reference_tail[:-suffix] if suffix else reference_tail
    b=hypothesis_tail[:-suffix] if suffix else hypothesis_tail
    if len(a)*len(b)>max_cells:raise ValueError('Reference comparison is too large; evaluate a shorter reviewed excerpt')
    previous=[(n,0,n,0) for n in range(len(b)+1)]
    for r,ref in enumerate(a,1):
        current=[(r,0,0,r)]
        for h,hyp in enumerate(b,1):
            diagonal=previous[h-1]
            if ref==hyp:current.append(diagonal)
            else:
                sub=(diagonal[0]+1,diagonal[1]+1,diagonal[2],diagonal[3])
                ins=(current[-1][0]+1,current[-1][1],current[-1][2]+1,current[-1][3])
                delete=(previous[h][0]+1,previous[h][1],previous[h][2],previous[h][3]+1)
                current.append(min((sub,ins,delete),key=lambda value:value[0]))
        previous=current
    distance,substitutions,insertions,deletions=previous[-1]
    return {'rate':distance/len(reference) if reference else None,'reference_length':len(reference),
            'edits':distance,'substitutions':substitutions,'insertions':insertions,'deletions':deletions}


def score_benchmark(values,output_dir=None):
    reference=values.get('reference')
    if not isinstance(reference,str) or not reference.strip() or len(reference)>100000:
        raise ValueError('Provide an independently reviewed reference of 1–100000 characters')
    if values.get('reference_source','human') not in {'human','reviewed'}:
        raise ValueError('Reference source must be human or reviewed')
    job_id=short_text(values.get('job_id'),'Recording ID',80);run_id=values.get('run_id','current')
    run=read_run(job_id,run_id,output_dir)
    ref=normalized_text(reference);hyp=normalized_text(run['text'])
    if not ref:raise ValueError('Reference must contain spoken words')
    report={'wer':edit_counts(ref.split(),hyp.split()),'cer':edit_counts(list(ref),list(hyp)),
            'normalization':'Unicode NFC, casefold, punctuation removed, whitespace collapsed; numbers are not rewritten',
            'metrics':run['metrics'],'settings':run['settings'],
            'reference_kind':values.get('reference_source','human'),'hypothesis_is_reference':reference.strip()==run['text'].strip()}
    id=uuid.uuid4().hex;name=short_text(values.get('name') or run['filename'],'Benchmark name',180)
    stamp=db.now()
    with db.connection() as connection:
        connection.execute('INSERT INTO benchmarks VALUES(?,?,?,?,?,?,?,?,?)',(id,name,job_id,run_id,reference,values.get('reference_source','human'),run['text'],json.dumps(report,ensure_ascii=False),stamp))
    return {'id':id,'name':name,'job_id':job_id,'run_id':run_id,'created_at':stamp,**report}


def list_benchmarks():
    with db.connection() as connection:
        rows=connection.execute('SELECT id,name,job_id,run_id,report,created_at FROM benchmarks ORDER BY created_at DESC LIMIT 100').fetchall()
    return [{key:row[key] for key in ('id','name','job_id','run_id','created_at')}|json.loads(row['report']) for row in rows]


def queue_estimate():
    settings=db.get_settings();parallel=max(1,settings['parallel_jobs'])
    with db.connection() as connection:
        recent=connection.execute("SELECT duration_s,inference_s FROM jobs WHERE status='done' AND duration_s>0 AND inference_s>0 ORDER BY finished_at DESC LIMIT 20").fetchall()
        queued=connection.execute("SELECT id,filename,duration_s FROM jobs WHERE status='queued' ORDER BY created_at,rowid").fetchall()
        active=connection.execute("SELECT * FROM jobs WHERE status IN ('preparing','processing') ORDER BY started_at").fetchall()
    ratios=[row['inference_s']/row['duration_s'] for row in recent]
    rate=statistics.median(ratios) if ratios else None
    if rate is None:return {'available':False,'remaining_s':None,'files':[],'reason':'Complete a recording to establish observed processing speed'}
    active_ends=[]
    unknown=False
    for row in active:
        public=db._public(row)
        remaining=public.get('eta_remaining_s')
        if remaining is None:
            total=row['total_segments'] or 0;completed=row['completed_segments'] or 0
            remaining=(row['duration_s'] or 0)*rate*(1-completed/total if total else 1)
        active_ends.append(remaining)
    slots=sorted(active_ends)[-parallel:]+[0.0]*max(0,parallel-len(active_ends))
    files=[]
    for row in queued:
        slot=min(range(parallel),key=lambda n:slots[n]);start=slots[slot]
        if not row['duration_s']:
            unknown=True;continue
        finish=start+row['duration_s']*rate
        files.append({'id':row['id'],'filename':row['filename'],'start_in_s':start,'finish_in_s':finish})
        slots[slot]=finish
    state=db.worker_state()
    paused=bool(settings.get('queue_paused'))
    available=bool(state.get('online') and state.get('engine_ready') and not paused)
    return {'available':available,'remaining_s':max(slots,default=0) if available else None,
            'files':files if available else [],'sample_files':len(ratios),'unknown_duration':unknown,
            'observed_rtf':rate,'reason':'Queue paused' if paused else ('Worker or model unavailable' if not available else 'Estimate from recent observed model time; load and settings can change it')}
