# Product and technical specification

**Scope:** local file-to-text transcription web app built around NVIDIA Nemotron 3.5 Transcribe 30B-A4B GGUF. Primary language Romanian; accept English and automatic language mode. Works offline after images/weights are downloaded. Host is Ryzen 9 9950X3D, 64 GB RAM, RTX 3080 10 GB on Ubuntu.

## Functional requirements

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | P0 | Drag/drop or file-picker upload of audio and video; MP3/WAV/FLAC/M4A/MP4/MKV/WEBM among allowlisted formats. |
| FR-02 | P0 | Stream large uploads to disk, cap size by configuration; display filename and upload error to user. |
| FR-03 | P0 | Use FFmpeg for video-to-audio extraction and mono 16 kHz PCM16 WAV transcoding. |
| FR-04 | P0 | Segment long recordings into configurable chunks (initial choices 60/120/300s); process in order; do not start another 21 GB model per chunk. |
| FR-05 | P0 | Language selector defaults to Romanian (`ro`), optional `en` / `auto`. |
| FR-06 | P0 | Queue jobs, show QUEUED/PREPARING/PROCESSING/DONE/FAILED, progress by completed segment; preserve results across restarts. |
| FR-07 | P0 | Export TXT and JSON with Unicode Romanian diacritics. |
| FR-08 | P1 | Export SRT with timestamps from model word alignment; if unavailable, do NOT invent millisecond precision. |
| FR-09 | P1 | Optional speaker labels; document that chunk-local speaker IDs can change on different segments. |
| FR-10 | P1 | Retry transient inference errors, bounded; permanent decoder/input errors fail clearly. |
| FR-11 | P0 | CPU/RAM backend with IQ4_NL works independently of any NVIDIA GPU support. |
| FR-12 | P0 target, R&D gate | Actual GPU+CPU MoE expert offload on RTX 3080; requires external patched runtime and measured verification, NOT supported upstream by default. |
| FR-13 | P2 | User can remove an upload and its derived results; add retention controls. |
| FR-14 | P2 | Language/domain prompt and names list without breaking punctuation; use SALM chat endpoint if needed. |

## Nonfunctional requirements

- Single-machine, single-user, localhost by default. No cloud dependency after provisioning; models/inference run locally.
- One worker / one model instance for initial deploy. SQLite for durable local queue; no heavy broker needed.
- Check that 21.35 GB weights plus runtime memory leave enough RAM under 64 GB. Storage must account for source uploads, PCM WAV temp files (~115 MB/hour at 16kHz PCM16) and results.
- Process untrusted audio with FFmpeg in a restricted non-root container; size/time limits; no shell interpolation of user filenames.
- Report RTFx (audio_seconds/processing_wall_seconds), p50/p95 per-segment latency, peak VRAM, peak RSS, WER/CER, and failure rate. These are measured metrics, not assumed targets.
- Native diarization over chunked files is **not guaranteed** to preserve speaker IDs; do not claim stable video-wide IDs without stitching algorithm validation.
- Use OpenMDW 1.1 model-license conditions; NVIDIA C++ runtime Apache-2.0.

## Non-goals for the first release

- Multi-tenant/public SaaS, internet-facing anonymous upload, Kubernetes/Keycloak integration.
- Real-time microphone streaming (this model processes whole recordings).
- Video understanding, translation, automatic speaker identity across chunk boundaries.
- Guaranteed >1x realtime on a 3080 using experimental offload before measuring.

## User journey

1. Opens localhost UI; engine status indicates ready or loading.
2. Selects one or several files, language `ro`, chunk length 60/120/300 s, optional diarization.
3. Each file creates a durable job; UI polls status; backend processes at most 1 inference request at a time.
4. WAV segments are sent to the NeMo HTTP server's documented `/v1/audio/transcriptions` endpoint with `response_format=verbose_json`.
5. On completion, user sees TXT and JSON; SRT appears only when valid aligned word timing was returned. Failures present an actionable reason.
