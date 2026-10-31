"""Durable FIFO. SQLite transactions coordinate API deletion and worker claims."""
import os
import json
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


def create_job(id, filename, input_path, language, segment_seconds, diarize, duration_s=None, output_language="source", number_style="natural"):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','preparing','processing')").fetchone()[0]
        if count >= int(os.getenv('MAX_PENDING_JOBS', '100')):
            raise ValueError('Queue is full; wait for a job to finish')
        db.execute('''INSERT INTO jobs
            (id,filename,input_path,status,language,segment_seconds,diarize,created_at,updated_at,duration_s,output_language,number_style)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (id, filename, input_path, 'queued', language, segment_seconds, int(diarize), now(), now(), duration_s, output_language, number_style))


def _public(row):
    if row is None:
        return None
    result = dict(row)
    result.pop('input_path', None)
    result['srt_available'] = result['status'] == 'done' and (DATA / 'results' / result['id'] / 'transcript.srt').is_file()
    return result


def get_job(id):
    with connection() as db:
        return _public(db.execute('SELECT * FROM jobs WHERE id=?', (id,)).fetchone())


def list_jobs(limit=100):
    with connection() as db:
        return [_public(x) for x in (db.execute('SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC') if limit is None else db.execute('SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?', (limit,)))]


def recover_jobs():
    # Caller MUST hold the host worker lock. A crash restarts the whole job.
    with connection() as db:
        db.execute("""UPDATE jobs SET status='queued',error=NULL,progress=0,total_segments=0,
            completed_segments=0,started_at=NULL,wall_s=NULL,inference_s=NULL,rtfx=NULL,updated_at=?
            WHERE status IN ('processing','preparing')""", (now(),))


def claim_job(max_active=1):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        # A second caller cannot claim another job while the first is active.
        if db.execute("SELECT count(*) FROM jobs WHERE status IN ('preparing','processing')").fetchone()[0] >= max_active:
            return None
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET status='preparing',updated_at=?,started_at=? WHERE id=?", (now(), now(), row['id']))
            return dict(row)
        return None


def update_job(id, **kwargs):
    allowed = {'status','progress','total_segments','completed_segments','error','duration_s','wall_s','inference_s','rtfx'}
    if not kwargs or not kwargs.keys() <= allowed:
        raise ValueError('Invalid job fields')
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


def retry_job(id):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (id,)).fetchone()
        if row is None:
            return None
        if row['status'] not in {'failed', 'done'}:
            raise ValueError('Only failed or completed recordings can be retried')
        if not Path(row['input_path']).is_file():
            raise ValueError('The original recording is missing; upload it again')
        count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','preparing','processing')").fetchone()[0]
        if count >= int(os.getenv('MAX_PENDING_JOBS', '100')):
            raise ValueError('Queue is full; wait for processing to finish')
        db.execute("""UPDATE jobs SET status='queued',error=NULL,progress=0,total_segments=0,
            completed_segments=0,started_at=NULL,wall_s=NULL,inference_s=NULL,rtfx=NULL,updated_at=?
            WHERE id=?""", (now(), id))
        return dict(row)


def get_settings():
    with connection() as db:
        row = db.execute('SELECT value FROM settings WHERE id=1').fetchone()
    return {**DEFAULTS, **(json.loads(row['value']) if row else {})}


def save_settings(values):
    if not isinstance(values, dict) or not values or not values.keys() <= DEFAULTS.keys():
        raise ValueError('Unknown settings fields')
    result = {**get_settings(), **values}
    if isinstance(result['parallel_jobs'], bool) or not isinstance(result['parallel_jobs'], int) or not 1 <= result['parallel_jobs'] <= CAPACITY:
        raise ValueError(f'Parallel jobs must be an integer from 1 to {CAPACITY}')
    if result['segment_seconds'] not in (60, 120, 300):
        raise ValueError('Segment length must be 60, 120 or 300 seconds')
    if result['input_language'] not in {*LANGUAGES, 'auto'} or result['output_language'] not in {*LANGUAGES, 'source'}:
        raise ValueError('Unsupported input or output language')
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
