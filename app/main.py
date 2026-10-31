import asyncio
import json
from datetime import datetime, timezone
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import db
from db import DATA, create_job, get_job, init_db, list_jobs
from media import probe_audio
from settings import LANGUAGES, CAPACITY

INPUT = DATA / 'uploads'
OUTPUT = DATA / 'results'
STATIC = Path(__file__).parent / 'static'
MAX_UPLOAD = int(os.getenv('MAX_UPLOAD_MB', '2048')) * 1024 * 1024
MIN_FREE = int(os.getenv('MIN_FREE_DISK_MB', '1024')) * 1024 * 1024
EXTS = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4a', '.mp4a', '.mp3', '.wav', '.flac', '.ogg', '.opus', '.aac'}


@asynccontextmanager
async def lifespan(app):
    INPUT.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(title='Nemotron Transcription Studio', lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=str(STATIC)), name='static')


class UploadLimitMiddleware:
    """Apply the cap before multipart spooling as well as during the final copy."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST' or scope['path'] != '/api/jobs':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        limit = MAX_UPLOAD + 1024 * 1024  # bounded multipart overhead
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length'}, 400)(scope, receive, send)
        if declared > limit:
            return await JSONResponse({'detail': 'File exceeds upload limit'}, 413)(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get('body', b''))
            if received > limit:
                raise HTTPException(413, 'File exceeds upload limit')
            return message
        await self.app(scope, bounded_receive, send)


app.add_middleware(UploadLimitMiddleware)


@app.middleware('http')
async def local_requests(request: Request, call_next):
    # Local browser mutations must originate from this UI, including on localhost.
    if request.method in {'POST', 'DELETE', 'PUT', 'PATCH'}:
        origin = request.headers.get('origin')
        if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and (
            urlsplit(origin).netloc != request.headers.get('host') or
            urlsplit(origin).scheme not in {'http', 'https'})):
            return JSONResponse({'detail': 'Cross-origin modification refused'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    return response


@app.get('/')
def index():
    return RedirectResponse('/upload')


def page():
    return FileResponse(STATIC / 'index.html')


for _page in ('upload','files','player','statistics','info','settings','corrections'):
    app.add_api_route('/'+_page, page, methods=['GET'], include_in_schema=False)


@app.get('/api/system')
async def system():
    status, ready = None, False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            res = await client.get(os.getenv('NEMO_URL', 'http://nemo:8080').rstrip('/') + '/ready')
        status = res.status_code
        ready = status == 200 and res.json().get('ready') is True
    except (httpx.RequestError, ValueError, AttributeError):
        pass
    usage = shutil.disk_usage(INPUT)
    telemetry = None
    try:
        candidate = json.loads((db.DATA / 'system.json').read_text())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(candidate['sampled_at'])).total_seconds()
        if 0 <= age < 15:
            telemetry = candidate
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        runtime = json.loads(Path(os.getenv('RUNTIME_LOCK_FILE', '/runtime-lock.json')).read_text())
    except (OSError, ValueError):
        runtime = None
    return {'engine_ready': ready, 'engine_status': status,
            'backend': os.getenv('NEMO_MODE', 'cpu'),
            'max_upload_mb': MAX_UPLOAD // (1024 * 1024),
            'disk_free_gib': round(usage.free / 1024**3, 2),
            'retention_days': int(os.getenv('RETENTION_DAYS', '0')),
            'telemetry': telemetry, 'runtime': runtime, 'settings':db.get_settings(), 'engine_capacity':CAPACITY, 'accepted_filetypes':sorted(EXTS)}


@app.post('/api/jobs', status_code=201)
async def upload(file: UploadFile = File(...), language: str | None = Form(None), segment_seconds: int | None = Form(None), diarize: bool | None = Form(None), output_language: str | None = Form(None), number_style: str | None = Form(None)):
    defaults = db.get_settings()
    number_style = number_style if number_style is not None else defaults['number_style']
    language = language if language is not None else defaults['input_language']
    output_language = output_language if output_language is not None else defaults['output_language']
    segment_seconds = segment_seconds if segment_seconds is not None else defaults['segment_seconds']
    diarize = diarize if diarize is not None else defaults['diarize']
    original = Path((file.filename or '').replace('\\', '/')).name[:180]
    ext = Path(original).suffix.lower()
    try:
        if number_style not in {'natural','digits'}:
            raise HTTPException(400,'Number formatting must be natural or digits')
        if ext not in EXTS:
            raise HTTPException(400, 'Unsupported extension. Upload audio or video only.')
        language = language.lower().strip()
        if language not in {*LANGUAGES, 'auto'}:
            raise HTTPException(400, 'Unsupported input language')
        output_language = output_language.lower().strip()
        if output_language not in {*LANGUAGES, 'source'}:
            raise HTTPException(400, 'Unsupported output language')
        if diarize and output_language not in {'source', language}:
            raise HTTPException(400, 'Speaker detection requires original-language output')
        if segment_seconds not in {60, 120, 300}:
            raise HTTPException(400, 'Segment size must be 60, 120 or 300 seconds')
        id = uuid.uuid4().hex
        dest = INPUT / (id + ext)
        size = 0
        try:
            with dest.open('xb') as fh:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, 'File exceeds upload limit')
                    if shutil.disk_usage(INPUT).free < len(chunk) + MIN_FREE:
                        raise HTTPException(507, 'Insufficient disk space; remove old jobs or free storage')
                    await asyncio.to_thread(fh.write, chunk)
            if not size:
                raise HTTPException(400, 'Empty file')
            try:
                duration = await asyncio.to_thread(probe_audio, dest)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            try:
                await asyncio.to_thread(create_job, id, original, str(dest), language, segment_seconds, diarize, duration, output_language, number_style)
            except ValueError as exc:
                raise HTTPException(429, str(exc)) from exc
            return {'id': id, 'status': 'queued'}
        except OSError as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(507, 'Unable to save upload; check free space and data permissions') from exc
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
    finally:
        await file.close()


@app.get('/api/jobs')
def jobs(all: bool = False):
    return {'jobs': list_jobs(limit=None if all else 100)}


@app.get('/api/stats')
def stats():
    return {**db.statistics(), **db.model_throughput()}


@app.get('/api/jobs/{id}')
def job(id: str):
    data = get_job(id)
    if not data:
        raise HTTPException(404, 'Job not found')
    return data


@app.delete('/api/jobs/{id}', status_code=204)
def delete(id: str):
    try:
        row = db.delete_job(id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not row:
        raise HTTPException(404, 'Job not found')
    # Only remove generated files beneath the configured data roots.
    path = Path(row['input_path'])
    if path.parent.resolve() == INPUT.resolve():
        path.unlink(missing_ok=True)
    shutil.rmtree(OUTPUT / row['id'], ignore_errors=True)


@app.get('/api/jobs/{id}/download/{format}')
def download(id: str, format: str):
    if format not in {'txt', 'json', 'srt'}:
        raise HTTPException(404, 'Unsupported export format')
    item = get_job(id)
    if not item or item['status'] != 'done':
        raise HTTPException(404, 'Completed job not found')
    path = OUTPUT / item['id'] / f'transcript.{format}'
    if not path.is_file():
        raise HTTPException(404, 'Result not found')
    if format == 'json':
        # Backfill playback mappings for older exports without changing the stored model timings.
        from worker import playback_words
        data = json.loads(path.read_text())
        for segment in data.get('segments', []):
            if 'playback_words' not in segment:
                segment['playback_words'] = playback_words(segment)
        return JSONResponse(data, headers={'Content-Disposition':FileResponse(path, filename=f'{Path(item["filename"]).stem[:90]}.json').headers['content-disposition']})
    return FileResponse(path, media_type='application/octet-stream', filename=f'{Path(item["filename"]).stem[:90]}.{format}')


@app.get('/api/jobs/{id}/media')
def media(id: str):
    """Serve original media with HTTP byte ranges for browser seeking."""
    with db.connection() as connection:
        row = connection.execute('SELECT filename,input_path FROM jobs WHERE id=?', (id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Recording not found')
    path = Path(row['input_path'])
    if path.parent.resolve() != INPUT.resolve() or not path.is_file():
        raise HTTPException(404, 'Recording not found')
    types = {'.m4a':'audio/mp4','.mp4a':'audio/mp4','.mp4':'video/mp4','.mp3':'audio/mpeg',
             '.wav':'audio/wav','.flac':'audio/flac','.webm':'video/webm','.ogg':'audio/ogg',
             '.opus':'audio/ogg','.mov':'video/quicktime','.mkv':'video/x-matroska'}
    return FileResponse(path, media_type=types.get(path.suffix.lower(), 'application/octet-stream'),
                        filename=row['filename'], content_disposition_type='inline')


@app.post('/api/jobs/{id}/retry', status_code=202)
def retry(id: str):
    try:
        row = db.retry_job(id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, 'Job not found')
    return {'id':id, 'status':'queued'}


@app.get('/api/settings')
def settings():
    return {'settings':db.get_settings(), 'languages':LANGUAGES, 'engine_capacity':CAPACITY,
            'active_jobs':sum(j['status'] in {'preparing','processing'} for j in db.list_jobs())}


@app.put('/api/settings')
def update_settings(values: dict):
    try:
        return {'settings':db.save_settings(values), 'engine_capacity':CAPACITY}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def completed_for_correction(id):
    job = get_job(id)
    if not job or job['status'] != 'done':
        raise HTTPException(409 if job else 404, 'A completed recording is required')
    return job


@app.get('/api/jobs/{id}/correction')
def correction(id: str):
    job = completed_for_correction(id)
    saved = db.get_correction(id)
    if saved:
        return {**saved,'filename':job['filename']}
    path = OUTPUT / id / 'transcript.txt'
    if not path.is_file():
        raise HTTPException(404, 'Transcript not available')
    return {'job_id':id,'text':path.read_text(encoding='utf-8'),'revision':0,'updated_at':None,'filename':job['filename']}


@app.put('/api/jobs/{id}/correction')
def update_correction(id: str, values: dict):
    completed_for_correction(id)
    text, revision = values.get('text'), values.get('revision')
    if not isinstance(text,str) or len(text)>2_000_000 or isinstance(revision,bool) or not isinstance(revision,int) or revision<0:
        raise HTTPException(400, 'A transcript of at most 2 million characters and a valid revision are required')
    try:
        return db.save_correction(id,text,revision)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@app.get('/api/jobs/{id}/correction/download/{format}')
def export_correction(id: str, format: str):
    job = completed_for_correction(id)
    value = correction(id)
    if format not in {'txt','json'}:
        raise HTTPException(404, 'Corrected text is available as TXT or JSON; original word timing does not apply to edited words')
    filename = f'{Path(job["filename"]).stem[:80]}-corrected.{format}'
    headers = {'Content-Disposition':FileResponse('unused',filename=filename).headers['content-disposition']}
    if format=='txt':
        return Response(value['text'],media_type='text/plain',headers=headers)
    return JSONResponse({'job_id':id,'original_filename':job['filename'],'text':value['text'],
                         'revision':value['revision'],'updated_at':value['updated_at'],
                         'timing_source':None,'note':'Analyst-edited text; original alignment is not attached to changed words.'},headers=headers)
