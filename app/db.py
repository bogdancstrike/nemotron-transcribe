"""Durable FIFO. SQLite transactions coordinate API deletion and worker claims."""
import os
import json
import time
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from settings import DEFAULTS, CAPACITY, LANGUAGES

DATA = Path(os.getenv('DATA_DIR', '/data'))
DB = DATA / 'jobs.sqlite3'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA journal_mode=WAL')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, input_path TEXT NOT NULL,
            status TEXT NOT NULL, language TEXT NOT NULL,
            segment_seconds INTEGER NOT NULL, diarize INTEGER NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0, total_segments INTEGER NOT NULL DEFAULT 0,
            completed_segments INTEGER NOT NULL DEFAULT 0, error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')
        db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO settings(id,value) VALUES (1,?)', (json.dumps(DEFAULTS),))
        db.execute('CREATE TABLE IF NOT EXISTS corrections (job_id TEXT PRIMARY KEY,text TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL)')
        existing = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
        db.execute('CREATE TABLE IF NOT EXISTS runtime_state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
        for column,definition in {'temperature':'REAL NOT NULL DEFAULT 0','seed':'INTEGER','finished_at':'TEXT','stage':"TEXT NOT NULL DEFAULT 'queued'", 'heartbeat_at':'TEXT', 'segment_started_at':'TEXT', 'eta_updated_at':'TEXT', 'eta_seconds':'REAL', 'eta_low_s':'REAL', 'eta_high_s':'REAL', 'eta_samples':'INTEGER', 'attempts':'INTEGER NOT NULL DEFAULT 0', 'error_code':'TEXT'}.items():
            if column not in existing:db.execute(f'ALTER TABLE jobs ADD COLUMN {column} {definition}')
        db.execute("UPDATE jobs SET finished_at=updated_at WHERE status IN ('done','failed') AND finished_at IS NULL")
        db.execute('CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status,created_at)')
        if 'guidance' not in existing:
            db.execute("ALTER TABLE jobs ADD COLUMN guidance TEXT NOT NULL DEFAULT ''")
        if 'number_style' not in existing:
            db.execute("ALTER TABLE jobs ADD COLUMN number_style TEXT NOT NULL DEFAULT 'natural'")
        if 'output_language' not in existing:
            db.execute("ALTER TABLE jobs ADD COLUMN output_language TEXT NOT NULL DEFAULT 'source'")
        db.execute('CREATE TABLE IF NOT EXISTS processing_intervals (job_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT NOT NULL)')
        if 'started_at' not in existing:
            db.execute('ALTER TABLE jobs ADD COLUMN started_at TEXT')
        for column in ('duration_s', 'wall_s', 'inference_s', 'rtfx'):
            if column not in existing:
                db.execute(f'ALTER TABLE jobs ADD COLUMN {column} REAL')
    from workspace_store import init_schema as workspace_schema
    from review_tools import init_schema as review_schema
    workspace_schema()
    review_schema()
    from alignment import init_schema as alignment_schema
    alignment_schema()


def create_job(id, filename, input_path, language, segment_seconds, diarize, duration_s=None, output_language="source", number_style="natural", guidance="", temperature=0.0, seed=None, processing_options=None):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','preparing','processing')").fetchone()[0]
        if count >= int(os.getenv('MAX_PENDING_JOBS', '100')):
            raise ValueError('Queue is full; wait for a job to finish')
        db.execute('''INSERT INTO jobs
            (id,filename,input_path,status,language,segment_seconds,diarize,created_at,updated_at,duration_s,output_language,number_style,guidance,temperature,seed,processing_options)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (id, filename, input_path, 'queued', language, segment_seconds, int(diarize), now(), now(), duration_s, output_language, number_style, guidance, temperature, seed, json.dumps(processing_options or {},ensure_ascii=False)))


def _public(row):
    if row is None:
        return None
    result = dict(row)
    input_path=result.pop('input_path', None)
    for key,default in [('processing_options',{}),('tags',[])]:
        if isinstance(result.get(key),str):
            try:result[key]=json.loads(result[key])
            except ValueError:result[key]=default
    result['archived']=bool(result.get('archived'))
    result['can_retry']=result['status'] in {'failed','done','cancelled'} and bool(input_path and Path(input_path).is_file())
    result['eta_remaining_s']=None
    if result['status']=='processing' and result.get('eta_seconds') and result.get('eta_updated_at') and result.get('stage')!='waiting_for_engine':
        elapsed=max(0,time.time()-datetime.fromisoformat(result['eta_updated_at']).timestamp())
        if elapsed<result['eta_seconds']+max(30,result['eta_seconds']*.2):
            result['eta_remaining_s']=max(1,result['eta_seconds']-elapsed)
    result['worker_stale']=False
    if result['status'] in {'processing','preparing'} and result.get('heartbeat_at'):
        result['worker_stale']=time.time()-datetime.fromisoformat(result['heartbeat_at']).timestamp()>20
        if result['worker_stale']:result['eta_remaining_s']=None
    result['srt_available'] = result['status'] == 'done' and (DATA / 'results' / result['id'] / 'transcript.srt').is_file()
    return result


def get_job(id):
    with connection() as db:
        return _public(db.execute('SELECT * FROM jobs WHERE id=?', (id,)).fetchone())


def list_jobs(limit=100):
    with connection() as db:
        return [_public(x) for x in (db.execute('SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC') if limit is None else db.execute('SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?', (limit,)))]


def recover_jobs():
    # Caller MUST hold the host worker lock. Completed chunks are restored from the checkpoint on restart.
    with connection() as db:
        db.execute("""UPDATE jobs SET status='queued',error=NULL,stage='recovering',
            segment_started_at=NULL,eta_seconds=NULL,eta_updated_at=NULL,updated_at=?
            WHERE status IN ('processing','preparing')""", (now(),))


def claim_job(max_active=1):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        # A second caller cannot claim another job while the first is active.
        if db.execute("SELECT count(*) FROM jobs WHERE status IN ('preparing','processing')").fetchone()[0] >= max_active:
            return None
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET status='preparing',stage='preparing',attempts=attempts+1,error_code=NULL,updated_at=?,started_at=COALESCE(started_at,?) WHERE id=?", (now(), now(), row['id']))
            return dict(row)
        return None


def update_job(id, **kwargs):
    allowed = {'status','progress','total_segments','completed_segments','error','duration_s','wall_s','inference_s','rtfx','stage','heartbeat_at','segment_started_at','eta_updated_at','eta_seconds','eta_low_s','eta_high_s','eta_samples','error_code','finished_at'}
    if not kwargs or not kwargs.keys() <= allowed:
        raise ValueError('Invalid job fields')
    if kwargs.get('status') in {'done','failed'}:kwargs.setdefault('finished_at',now())
    kwargs['updated_at'] = now()
    clause = ','.join(f'{key}=?' for key in kwargs)
    with connection() as db:
        db.execute(f'UPDATE jobs SET {clause} WHERE id=?', [*kwargs.values(), id])


def delete_job(id):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (id,)).fetchone()
        if row is None:
            return None
        if row['status'] in {'preparing', 'processing'}:
            raise ValueError('Active jobs cannot be deleted; wait for processing to finish')
        db.execute('DELETE FROM review_annotations WHERE job_id=?',(id,))
        db.execute('DELETE FROM corrections WHERE job_id=?', (id,))
        db.execute('DELETE FROM processing_intervals WHERE job_id=?', (id,))
        db.execute('DELETE FROM jobs WHERE id=?', (id,))
        return dict(row)


def expired_jobs(days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connection() as db:
        return [row['id'] for row in db.execute(
            "SELECT id FROM jobs WHERE status IN ('done','failed') AND updated_at < ?", (cutoff,))]


def statistics():
    """Weighted aggregate factors over completed retained jobs, never average ratios."""
    with connection() as db:
        counts = {row['status']: row['n'] for row in db.execute('SELECT status,count(*) AS n FROM jobs GROUP BY status')}
        totals = dict(db.execute("""SELECT COALESCE(SUM(duration_s),0) AS audio_s,
            COALESCE(SUM(wall_s),0) AS wall_s, COALESCE(SUM(inference_s),0) AS inference_s
            FROM jobs WHERE status='done'""").fetchone())
        history = [dict(row) for row in db.execute("""SELECT substr(updated_at,1,10) AS day,
            COUNT(*) AS files, SUM(duration_s) AS audio_s FROM jobs WHERE status='done'
            AND updated_at >= ? GROUP BY day ORDER BY day""",
            ((datetime.now(timezone.utc) - timedelta(days=6)).strftime('%Y-%m-%d'),))]
    audio, wall, inference = totals['audio_s'], totals['wall_s'], totals['inference_s']
    return {'counts': counts, **totals, 'rtfx': audio / wall if wall else None,
            'rtf': wall / audio if audio else None,
            'inference_rtfx': audio / inference if inference else None,
            'files_per_hour': counts.get('done', 0) * 3600 / wall if wall else None,
            'history': history, 'scope': 'completed_retained_jobs'}


def retry_job(id, use_current_settings=False):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (id,)).fetchone()
        if row is None:
            return None
        if row['status'] not in {'failed', 'done','cancelled'}:
            raise ValueError('Only failed, cancelled or completed recordings can be retried')
        if not Path(row['input_path']).is_file():
            raise ValueError('The original recording is missing; upload it again')
        count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','preparing','processing')").fetchone()[0]
        if count >= int(os.getenv('MAX_PENDING_JOBS', '100')):
            raise ValueError('Queue is full; wait for processing to finish')
        db.execute("""UPDATE jobs SET status='queued',error=NULL,progress=0,total_segments=0,
            completed_segments=0,stage='queued',error_code=NULL,eta_seconds=NULL,started_at=NULL,finished_at=NULL,heartbeat_at=NULL,wall_s=NULL,inference_s=NULL,rtfx=NULL,updated_at=?
            WHERE id=?""", (now(), id))
        if use_current_settings:
            from workspace_store import snapshot_options, guidance_for
            settings=get_settings()
            db.execute('UPDATE jobs SET processing_options=?,guidance=? WHERE id=?',[json.dumps(snapshot_options(settings),ensure_ascii=False),guidance_for(settings),id])
            db.execute('''UPDATE jobs SET language=?,output_language=?,number_style=?,guidance=?,temperature=?,seed=?,segment_seconds=?,diarize=? WHERE id=?''',
                [settings['input_language'],settings['output_language'],settings['number_style'],guidance_for(settings),settings['temperature'],settings['seed'],settings['segment_seconds'],int(settings['diarize']),id])
        return dict(row)


def get_settings():
    with connection() as db:
        row = db.execute('SELECT value FROM settings WHERE id=1').fetchone()
    return {**DEFAULTS, **(json.loads(row['value']) if row else {})}


def save_settings(values):
    if not isinstance(values, dict) or not values or not values.keys() <= DEFAULTS.keys():
        raise ValueError('Unknown settings fields')
    from workspace_store import validate_settings
    result = validate_settings(values,get_settings())
    if isinstance(result['parallel_jobs'], bool) or not isinstance(result['parallel_jobs'], int) or not 1 <= result['parallel_jobs'] <= CAPACITY:
        raise ValueError(f'Parallel jobs must be an integer from 1 to {CAPACITY}')
    if isinstance(result['temperature'],bool) or not isinstance(result['temperature'],(int,float)) or not 0<=result['temperature']<=1:
        raise ValueError('Temperature must be a number from 0 to 1')
    if result['seed'] is not None and (isinstance(result['seed'],bool) or not isinstance(result['seed'],int) or not 0<=result['seed']<=2147483647):
        raise ValueError('Seed must be empty or an integer from 0 to 2147483647')
    if result['chunk_mode'] not in {'fixed','silence'}:
        raise ValueError('Chunk mode must be fixed or silence')
    if isinstance(result['overlap_seconds'],bool) or not isinstance(result['overlap_seconds'],(int,float)) or not 0<=result['overlap_seconds']<=10:
        raise ValueError('Chunk overlap must be from 0 to 10 seconds')
    if result['channel_mode'] not in {'mix','separate'}:
        raise ValueError('Channel mode must be mix or separate')
    if not isinstance(result['vocabulary_ids'],list) or len(result['vocabulary_ids'])>20 or any(not isinstance(id,str) or len(id)>100 for id in result['vocabulary_ids']):
        raise ValueError('Choose at most 20 vocabulary lists')
    if result['segment_seconds'] not in (60, 120, 300):
        raise ValueError('Segment length must be 60, 120 or 300 seconds')
    if result['input_language'] not in {*LANGUAGES, 'auto'} or result['output_language'] not in {*LANGUAGES, 'source'}:
        raise ValueError('Unsupported input or output language')
    if not isinstance(result['queue_paused'], bool):
        raise ValueError('Queue pause must be a boolean')
    if not isinstance(result['guidance'],str) or len(result['guidance'])>4000 or '<|' in result['guidance'] or '|>' in result['guidance']:
        raise ValueError('Guidance must contain at most 4000 characters and no model control tokens')
    if result['number_style'] not in {'natural','digits'}:
        raise ValueError('Number formatting must be natural or digits')
    if not isinstance(result['diarize'], bool):
        raise ValueError('Speaker detection must be a boolean')
    if result['diarize'] and result['output_language'] not in {'source', result['input_language']}:
        raise ValueError('Speaker detection requires original-language output')
    with connection() as db:
        db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(result),))
    return result


def record_interval(id, started, ended):
    with connection() as db:
        db.execute('INSERT INTO processing_intervals VALUES (?,?,?)', (id, started, ended))


def aggregate_throughput():
    with connection() as db:
        intervals = [(row['started_at'], row['ended_at']) for row in db.execute("""SELECT i.started_at,i.ended_at
            FROM processing_intervals i JOIN jobs j ON j.id=i.job_id WHERE j.status='done' ORDER BY i.started_at""")]
    merged = []
    for start, end in intervals:
        start, end = datetime.fromisoformat(start).timestamp(), datetime.fromisoformat(end).timestamp()
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start,end])
    return sum(end-start for start,end in merged)


def model_throughput():
    """Union traced request intervals; exclude files without complete request coverage."""
    with connection() as db:
        rows = list(db.execute("""SELECT i.job_id,i.started_at,i.ended_at,j.duration_s,j.inference_s
            FROM processing_intervals i JOIN jobs j ON j.id=i.job_id WHERE j.status='done'"""))
    groups = {}
    for row in rows:
        group = groups.setdefault(row['job_id'], {'audio':row['duration_s'] or 0, 'inference':row['inference_s'] or 0, 'intervals':[]})
        group['intervals'].append((datetime.fromisoformat(row['started_at']).timestamp(), datetime.fromisoformat(row['ended_at']).timestamp()))
    complete = [g for g in groups.values() if sum(b-a for a,b in g['intervals']) >= g['inference']*.98]
    merged = []
    for start,end in sorted(interval for group in complete for interval in group['intervals']):
        if merged and start <= merged[-1][1]:merged[-1][1] = max(merged[-1][1],end)
        else:merged.append([start,end])
    busy = sum(b-a for a,b in merged)
    audio = sum(g['audio'] for g in complete)
    return {'model_busy_s':busy, 'traced_audio_s':audio, 'model_traced_files':len(complete),
            'model_throughput_rtfx':audio/busy if busy else None}


def get_correction(id):
    with connection() as db:
        row = db.execute('SELECT * FROM corrections WHERE job_id=?', (id,)).fetchone()
    return dict(row) if row else None


def save_correction(id, text, expected_revision):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM corrections WHERE job_id=?', (id,)).fetchone()
        revision = row['revision'] if row else 0
        if row and row['text'] == text:
            return dict(row)
        if revision != expected_revision:
            raise ValueError('Another tab saved a newer revision. Your draft has not been overwritten.')
        db.execute('''INSERT INTO corrections(job_id,text,revision,updated_at) VALUES (?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET text=excluded.text,revision=excluded.revision,updated_at=excluded.updated_at''',
            (id,text,revision+1,now()))
        return dict(db.execute('SELECT * FROM corrections WHERE job_id=?',(id,)).fetchone())


def worker_heartbeat(active_ids,engine_ready,stopping=False):
    value={'heartbeat_at':now(),'active_jobs':len(active_ids),'engine_ready':engine_ready,'stopping':stopping}
    with connection() as db:
        db.execute('INSERT INTO runtime_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(json.dumps(value),))
        if active_ids:
            placeholders=','.join('?' for _ in active_ids)
            db.execute(f'UPDATE jobs SET heartbeat_at=? WHERE id IN ({placeholders})',[now(),*active_ids])


def worker_state():
    with connection() as db:
        row=db.execute('SELECT value FROM runtime_state WHERE id=1').fetchone()
    value=json.loads(row['value']) if row else {}
    value['online']=bool(value.get('heartbeat_at') and time.time()-datetime.fromisoformat(value['heartbeat_at']).timestamp()<20 and not value.get('stopping'))
    return value


def backup_database():
    if not DB.is_file():return
    directory=DATA/'backups';directory.mkdir(parents=True,exist_ok=True)
    name=datetime.now(timezone.utc).strftime('jobs-%Y-%m-%d.sqlite3')
    target=directory/name
    if target.exists():return
    temporary=target.with_suffix('.tmp')
    with sqlite3.connect(DB) as source, sqlite3.connect(temporary) as backup:
        source.backup(backup)
    temporary.replace(target)
    for expired in sorted(directory.glob('jobs-*.sqlite3'))[:-3]:expired.unlink()


def list_jobs_page(page=1,page_size=10,status='all',sort='newest',project=None,tag=None,archive='active'):
    orders={'newest':'created_at DESC,rowid DESC','oldest':'created_at ASC,rowid ASC',
            'name':'filename COLLATE NOCASE,created_at DESC',
            'status':"CASE status WHEN 'queued' THEN 0 WHEN 'preparing' THEN 1 WHEN 'processing' THEN 1 WHEN 'failed' THEN 3 ELSE 2 END,created_at DESC"}
    if sort not in orders or status not in {'all','queued','processing','preparing','done','failed','paused','cancelled'}:
        raise ValueError('Unsupported status or sort order')
    if not 1<=page_size<=100 or page<1:raise ValueError('Invalid pagination')
    clauses,params=[],[]
    if archive not in {'active','archived','all'}:raise ValueError('Unsupported archive filter')
    if archive!='all':clauses.append('archived=?');params.append(int(archive=='archived'))
    if project is not None:clauses.append('project=?');params.append(project)
    if tag:
        if len(tag)>80:raise ValueError('Tag is too long')
        clauses.append('EXISTS(SELECT 1 FROM json_each(jobs.tags) WHERE value=?)');params.append(tag)
    base_where=' WHERE '+' AND '.join(clauses) if clauses else ''
    base_params=list(params)
    if status=='processing':clauses.append("status IN ('preparing','processing')")
    elif status!='all':clauses.append('status=?');params.append(status)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    with connection() as db:
        total=db.execute('SELECT count(*) FROM jobs'+where,params).fetchone()[0]
        counts={row['status']:row['count'] for row in db.execute('SELECT status,count(*) AS count FROM jobs'+base_where+' GROUP BY status',base_params)}
        pages=max(1,(total+page_size-1)//page_size);page=min(page,pages)
        rows=db.execute('SELECT * FROM jobs'+where+' ORDER BY '+orders[sort]+' LIMIT ? OFFSET ?', [*params,page_size,(page-1)*page_size]).fetchall()
    return {'jobs':[_public(row) for row in rows],'total':total,'counts':counts,'page':page,'page_size':page_size,'pages':pages}
