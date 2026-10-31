# API contract and security notes

## Public local service: `web:7860`

### `POST /api/jobs` multipart

Form fields:
- `file`: an allowed audio/video file (required, streamed to disk).
- `language`: `ro` default; `en` or `auto` supported by initial UI.
- `segment_seconds`: 60, 120 (default) or 300.
- `diarize`: boolean false by default.

`201`: `{ "id": "uuidhex", "status": "queued" }`; `400` unsupported extension/bad input, `413` upload exceeds configured cap.

### `GET /api/jobs`

`200`: `{ "jobs": [{"id":"...", "status":"queued|preparing|processing|done|failed", "filename":"...", "progress":0, "total_segments":0, "completed_segments":0, "language":"ro"}] }`. In a public deployment, omit raw filesystem paths and internal exception details.

### `GET /api/jobs/{id}`

`200`: job metadata. `404` unknown id.

### `GET /api/jobs/{id}/download/{txt|json|srt}`

`200` file download for completed job and existing artifact; `404` when not available. `srt` may intentionally be absent on alignment failure.

### `GET /api/system`

`200`: `{ "engine_ready": true | false, "engine_status": integer | null }`.

## Internal NeMo HTTP contract

`POST http://nemo:8080/v1/audio/transcriptions` multipart with WAV file, `language=ro`, `response_format=verbose_json`, optionally `diarization=true`.

Returns `text` and `words` with `word`, `start`, `end` and optional speaker (seconds relative to the chunk). The speech-LLM's confidence may be zero; do not treat it as a calibrated probability. See official `docs/api.md`.

## Security boundaries

**MVP = localhost single user**, not a publicly exposed upload server. Bind `127.0.0.1:${WEB_PORT}:7860`; internal inference server has no published host ports. Do not attach authless local-only code to a public reverse proxy without authentication, TLS, rate limits, quotas and isolation.

- Upload abuse: cap streaming size, enforce extension allowlist AND probe actual decodable stream. Restrict duration, FFmpeg subprocess timeout, disk quota, CPU consumption; consider container/seccomp restrictions.
- Path traversal: never store under raw original filename; use server-generated UUID. Format allowlists and `Path.name` for user-facing download filenames.
- Command injection: `subprocess.run([...], shell=False)` only; model command arguments fixed by operator-controlled env.
- Information disclosure: do not expose DB `input_path` to unauthenticated remote clients; sanitize backend errors; avoid storing credentials in logs.
- Availability: one worker; bounded retries, timeouts; prevent 5GB input from exhausting disk or indefinite audio on no-length containers.
- Privacy: locally stored media is not automatically erased; define retention policy for future shared deployments.
- Dependencies: pin Docker base image digests, Python requirements, upstream commit and HF snapshot in production. Model download uses TLS, optional HF token.
