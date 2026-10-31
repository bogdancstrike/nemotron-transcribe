"""Durable FIFO. SQLite transactions coordinate API deletion and worker claims."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        existing = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
        if 'started_at' not in existing:
            db.execute('ALTER TABLE jobs ADD COLUMN started_at TEXT')
        for column in ('duration_s', 'wall_s', 'inference_s', 'rtfx'):
            if column not in existing:
                db.execute(f'ALTER TABLE jobs ADD COLUMN {column} REAL')


def create_job(id, filename, input_path, language, segment_seconds, diarize, duration_s=None):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','preparing','processing')").fetchone()[0]
        if count >= int(os.getenv('MAX_PENDING_JOBS', '100')):
            raise ValueError('Queue is full; wait for a job to finish')
        db.execute('''INSERT INTO jobs
            (id,filename,input_path,status,language,segment_seconds,diarize,created_at,updated_at,duration_s)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (id, filename, input_path, 'queued', language, segment_seconds, int(diarize), now(), now(), duration_s))


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
        return [_public(x) for x in db.execute('SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?', (limit,))]


def recover_jobs():
    # Caller MUST hold the host worker lock. A crash restarts the whole job.
    with connection() as db:
        db.execute("""UPDATE jobs SET status='queued',error=NULL,progress=0,total_segments=0,
            completed_segments=0,started_at=NULL,wall_s=NULL,inference_s=NULL,rtfx=NULL,updated_at=?
            WHERE status IN ('processing','preparing')""", (now(),))


def claim_job():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        # A second caller cannot claim another job while the first is active.
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('preparing','processing') LIMIT 1").fetchone():
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
