<div align="center">

# Nemotron Transcribe

### Your recordings. Your hardware. Your words.

A private transcription studio for **Nemotron 3.5 Transcribe 30B-A4B**.<br>
Built for **RTX 3080 10 GB + 64 GB RAM**, with a reproducible CPU baseline and experimental expert offloading.

[![Tests](https://github.com/bogdancstrike/nemotron-transcribe/actions/workflows/tests.yml/badge.svg)](https://github.com/bogdancstrike/nemotron-transcribe/actions/workflows/tests.yml)
![Local](https://img.shields.io/badge/inference-local-226b45?style=flat-square)
![Docker Compose](https://img.shields.io/badge/deployment-Docker_Compose-2496ed?style=flat-square)
![Romanian](https://img.shields.io/badge/Romanian-UTF--8-7656a8?style=flat-square)
![Model](https://img.shields.io/badge/model-IQ4__NL-555?style=flat-square)

[Quick start](#quick-start) · [The studio](#the-studio) · [Measured performance](#measured-performance) · [Hardware](#hardware-and-offloading) · [Operations](#operations)

</div>

![Nemotron transcription studio](assets/upload.png)

## The studio

Seven focused pages. One local workspace.

| Tab | What you get |
| :--- | :--- |
| **Upload** | A wide, centered file chooser. M4A first, plus MP4, WAV, MP3, FLAC and more. Multiple uploads, real upload progress, and automatic navigation to the new file. |
| **Files** | Status views, server-side pagination (10 rows by default), added/started/ended timestamps, progress, ETA, processing time, RTF/RTFx, queue pause/resume and retry with original or current settings. |
| **Player** | Audio/video beside the full transcript. Matched words follow real model alignment. Click to seek, view/filter speakers, adjust playback-only sync offset, and change speed or automatic scrolling. |
| **Statistics** | Per-file and aggregate processing time, RTF and RTFx. Totals use summed audio and processing times. |
| **Corrections** | A compact editable transcript with live spoken-word highlighting, click-to-seek, a right-click playback menu, find/replace, undo/redo, autosave, revision protection, TXT/JSON export and local draft recovery. |
| **Info** | Deployment revisions, every accepted file type, host CPU/RAM, model RSS, storage and GPU memory, load, power and temperature. |
| **Settings** | Parallel processing levels 1, 2, 4, 8, 16 or custom within capacity; chunk size, input/output languages, temperature, sampling seed, guidance examples, number formatting and speaker defaults. |

**Romanian by default.** Diacritics remain intact throughout upload, transcription, playback and export. Choose an input language or automatic detection, independently of the output language. Keep the spoken language or request a translation. Optional speaker identification uses one stateful encoder pass over the complete file, with consistent file-wide IDs (up to 60 minutes). Important numeric values can be requested as digits; always verify critical amounts against the recording.

**No invented timestamps.** Matching words can follow genuine model timestamps even when other text is unaligned. Untimed text remains readable. Complete SRT is exported only when alignment covers the full transcript. Unchanged words in analyst edits can retain their original timing; new or changed words stay untimed. Translations may have no word timing.

## Screenshots

Fresh captures from the running Docker deployment. Job status and resource readings are real snapshots, not demo values.

### Upload — centered file chooser

![Upload — centered file chooser](assets/upload.png)

### Files — timestamps, throughput, recovery and pagination

![Files — timestamps, throughput, recovery and pagination](assets/files.png)

### Player — synchronized reading and playback

![Player — synchronized reading and playback](assets/player.png)

The speaker view uses IDs from a single pass over the complete recording:

![Player with file-wide speaker groups](assets/player-speakers.png)

### Statistics — measured per-file and combined throughput

![Statistics — measured per-file and combined throughput](assets/statistics.png)

### Corrections — editable word sync, autosave and export

![Corrections — editable word sync, autosave and export](assets/corrections.png)

Right-click a transcript word for playback and editing actions:

![Corrections contextual menu](assets/corrections-menu.png)

### Info — hardware consumption, deployment and accepted formats

![Info — hardware consumption, deployment and accepted formats](assets/info.png)

### Settings — model controls, guidance and processing presets

![Settings — model controls, guidance and processing presets](assets/settings.png)

## Quick start

Requirements: Linux, Docker Engine with the Compose plugin, **64 GB RAM**, and enough disk for the **21.35 GB model pair**, build layers and your recordings. Allow **at least 60 GB free** for a new setup; CUDA builds may need more.

```bash
git clone git@github.com:bogdancstrike/nemotron-transcribe.git
cd nemotron-transcribe
docker compose up --build -d
```

Open **[localhost:7860](http://127.0.0.1:7860)**.

The first explicit `docker compose up` builds the pinned runtime, downloads the exact decoder **and mandatory encoder**, verifies SHA-256 hashes, prepares writable data directories, and starts the web app, model server, worker and resource monitor. First provisioning takes time. Model weights and uploaded recordings are **never included in Git or Docker images**.

The default deployment uses the **tested RTX 3080 hybrid path**, with routed experts in system RAM. Install NVIDIA Container Toolkit and use a CUDA 13-compatible driver (tested: 580.178.04). For a GPU-independent CPU deployment, run `docker compose -f compose.yaml up --build -d`.

```bash
# Watch provisioning and model startup
docker compose logs -f model-download nemo

# Optional settings; defaults already work
cp .env.example .env
```

Existing complete model files are verified locally on subsequent starts, without contacting Hugging Face. After dependencies and weights are provisioned, transcription works offline.

## Measured performance

Measured on **Ryzen 9 9950X3D**, **64 GB RAM**, Ubuntu, using the exact IQ4_NL/F16 pair. The CPU results below use real Romanian recordings. The hybrid comparison uses the identical 60-second WAV and the same pinned model pair.

| CPU test | Result |
| :--- | ---: |
| Same 60-second news WAV, 3 sequential runs | **2.86× median RTFx** |
| Peak model process RSS across those runs | **35,589 MiB** |
| Inference process GPU allocation | **0 MiB** |
| Five additional 60-second M4A excerpts | **5/5 succeeded** |
| Median RTFx across the five excerpts | **3.16×** |

| Source recording · 60-second excerpt | Processing time | RTFx |
| :--- | ---: | ---: |
| `TpUDm4ay-B8.m4a` | 17.50 s | 3.43× |
| `oBcyZg0mNJo.m4a` | 19.00 s | 3.16× |
| `dyVQpIUe1zc.m4a` | 19.07 s | 3.15× |
| `fanN33YW3fc.m4a` | 18.81 s | 3.19× |
| `raE1bfapRhI.m4a` | 21.11 s | 2.84× |

**RTFx = audio duration ÷ processing time**; above 1× is faster than real time. **RTF = processing time ÷ audio duration**; below 1 is faster. Processing includes media preparation, model requests and export; upload, queue wait and model startup are excluded.

These five results use excerpts starting 10 seconds into each recording. They **do not establish full-recording throughput**. All five requested full recordings have also completed; their GPU results are below. WER/CER are unreported because independent reference transcripts have not been supplied. A model transcript is not a reference transcript. The supplied amount example is tracked as a targeted regression, separately from corpus-wide WER/CER.

### RTX 3080 hybrid proof

Three sequential 60-second Romanian requests succeeded with the patched runtime in the initial single-slot configuration:

| Measurement | Result |
| :--- | ---: |
| Median warm model-request RTFx | **7.41×** |
| Request time | **8.07–8.12 seconds** |
| Peak model VRAM | **6,006 MiB** |
| Peak total VRAM, including desktop | **6,941 MiB** |
| Peak model process RSS | **17,034 MiB** |
| Peak GPU utilization | **100%** |

CPU inference on the same clip took about 21 seconds. The hybrid and CPU transcripts matched across all **179 normalized words**; punctuation differed. This is agreement between runtimes, **not ground-truth accuracy**. The hybrid figures exclude UI/queue/preparation time. Full-recording results are listed below.

### Full recordings · 5/5 completed

All five requested M4A recordings completed through the real RTX 3080 hybrid deployment:

| Recording | Audio duration | Processing time | Per-file RTFx |
| :--- | ---: | ---: | ---: |
| `TpUDm4ay-B8.m4a` | 2h 00m 09s | 26m 34s | 4.52× |
| `oBcyZg0mNJo.m4a` | 59m 48s | 20m 22s | 2.94× |
| `dyVQpIUe1zc.m4a` | 55m 28s | 25m 39s | 2.16× |
| `fanN33YW3fc.m4a` | 53m 44s | 24m 22s | 2.20× |
| `raE1bfapRhI.m4a` | 51m 44s | 18m 20s | 2.82× |

These runs included concurrent work, worker restarts and an overnight deployment interruption. Processing time accumulates across compatible checkpoints and excludes stopped time. This is an **operational soak test**, rather than a controlled comparison between concurrency levels. Per-file times include shared-model contention; do not sum their RTFx values. Statistics separately measures overlapping model-request intervals once for combined throughput. Independent reference transcripts are still needed to measure WER/CER.

## Hardware and offloading

| Component | Configuration |
| :--- | :--- |
| Model | NVIDIA Nemotron 3.5 Transcribe 30B-A4B |
| Decoder | `NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-AD-IQ4_NL.gguf` · 19,678,656,672 bytes |
| Encoder | `NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-Encoder-F16.gguf` · 1,670,410,080 bytes |
| Runtime | NVIDIA/NeMo-Speech.cpp, pinned to `20999cc921d2b14aa58708f61756a8b2e787ba20` |
| Model snapshot | `dbd06c5cad7a2f33c8e06be2fb3ef814dbd9aa59` |
| Target GPU | RTX 3080 · 10 GB · compute capability 8.6 |
| Concurrency | One model server, one host worker, 16 decode slots with a fixed 16,384-token shared pool; file concurrency defaults to 1 |

The complete decoder and encoder do not fit in 10 GB VRAM. Stock NeMo-Speech.cpp does not expose the required CPU expert-placement setting. The local Apache-2.0 patch introduces `asr.salm.n_cpu_moe`, uses llama.cpp's existing CPU expert tensor helper, enables memory mapping, and selects the standard mixed-device scheduler for the Mamba/Transformer decoder.

`n_cpu_moe: 48` means routed-expert weights in the **first 48 decoder block indices**, not “48 MoE layers.” Attention, Mamba, router, shared-expert and encoder tensors retain their default GPU placement. Speculative decoding is disabled. The patch also fixes untagged multi-speaker alignment: plain transcript text aligns against all audio frames rather than being squeezed into one predicted speaker’s activity windows. File-wide speaker IDs are assigned separately from the encoder’s stateful speaker pass.

**Default GPU launch** — requires NVIDIA Container Toolkit and a compatible driver:

```bash
docker compose up --build -d
```

The corrected patch passed three successive real-model requests on the target GPU. All five requested full recordings passed the operational soak test above; independent Romanian accuracy evaluation remains open. Inspect [runtime.lock.json](runtime.lock.json), the [patch](runtime/hybrid.patch), and [runtime attribution](runtime/NOTICE.md) for exact provenance.

## Operations

```bash
# Logs and service state
docker compose ps
docker compose logs -f worker web

# Restart services; uploads and finished exports remain on disk
docker compose restart

# Stop the stack without deleting recordings or weights
docker compose down
```

Files live in `./data`; models live in `./models`. Neither directory is tracked. Keep the same data directory across restarts. A host lock prevents a second worker from recovering active jobs. Completed segments are checkpointed atomically. After a worker restart, compatible checkpoints resume at the first unfinished segment. A changed backend or transcription setting starts a fresh run. Files offers **Retry** with the saved settings, or **Retry with current settings** to apply changes from Settings. Pausing the queue stops new admissions while active recordings finish. An engine disconnect waits for recovery before safely resuming; uncertain live-request timeouts fail clearly instead of submitting duplicates.

The worker sends a heartbeat and records preparation, transcription, file-wide speaker identification and export stages. Files flags disconnected workers and preserves saved progress. Remaining-time estimates use the last five completed chunks and adapt to current model contention; they are hidden when stale or unavailable. No ETA is fabricated before the first completed chunk. SIGTERM finishes the current model request, checkpoints it, and requeues remaining work. Docker allows a two-minute graceful stop; a hard kill can require repeating the in-flight chunk.

Daily SQLite snapshots are created in `data/backups/` with the latest three snapshots retained. These back up **job/settings/review metadata**; protect `data/uploads/` and `data/results/` separately. To restore, stop web and worker, back up the current database and its WAL/SHM files, then replace the database with a snapshot and remove the obsolete WAL/SHM files before restarting. Restoring an older snapshot also restores older review revisions.

The UI binds only to **127.0.0.1**. The inference service has **no published host port**. This is a single-user local tool; public access needs a real authentication/reverse-proxy design.

Limits are configurable in `.env.example`: upload size, recording duration, disk reserve, queue size and retention. Retention is off by default. Deleting a queued or completed file removes its upload and derived artifacts; active files cannot be deleted.

## Tests and benchmarks

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r app/requirements.txt pytest pyyaml psutil
.venv/bin/pytest -q

# Real-model performance through the upload/job/download API
.venv/bin/python scripts/benchmark.py your-recording.m4a \
  --output evidence/my-benchmark --segment-seconds 60

# Add --reference reference.txt to compute Romanian WER/CER.
```

The application suite currently passes **73 tests**, including checkpoint recovery, failed/completed-file retries, model-disconnect recovery, actual FFmpeg conversion of a synthetic 60-minute MP4, queue recovery and locking, Unicode exports, byte-range playback, invalid alignment, bounded retries and OOM handling. That long-file test uses mocked inference. Browser checks separately exercise all seven routes, mobile layout, real media seeking/highlighting, correction autosave/export/offline drafts, and the keyboard-accessible contextual menu. A real three-minute M4A test verified live ETA, nonzero temperature, seeded sampling, queue pause, graceful worker restart and reuse of two saved chunks. A separate two-chunk recording exercised the complete-file speaker pass. Raw local evidence is stored under `evidence/` and excluded from Git because it can contain recordings and transcripts.

## Architecture

```mermaid
flowchart LR
    Browser[Local browser] --> Web[FastAPI]
    Web --> Queue[(SQLite queue)]
    Web --> Files[(Uploads and exports)]
    Queue --> Worker[Single worker]
    Worker --> FFmpeg[FFmpeg chunks]
    FFmpeg --> NeMo[One NeMo model server]
    NeMo --> Worker
    Worker --> Files
    Monitor[Read-only resource monitor] --> Web
```

## A workflow that stays out of your way

- Pages have real URLs: `/upload`, `/files`, `/player`, `/statistics`, `/corrections`, `/info`, `/settings`. Reload and browser Back/Forward keep your place. Player and Corrections URLs retain the selected recording.
- Upload accepts **M4A, MP4, MP4A, WAV, MP3, FLAC, MKV, WEBM, MOV, AVI, OGG, OPUS and AAC**. FFprobe validates the actual audio stream; an extension alone is not enough.
- Files and Statistics use bounded server-side pages, starting at **10 rows per page**. Files can be filtered by queued, transcribing, completed or failed status, and sorted independently. Every record shows when it was added, started and ended alongside processing time, RTF and RTFx. Timestamps use your browser’s local timezone; hover for the full date.
- Corrections keep the original model transcript intact. Reviewed text has revision checks, autosave and per-tab draft recovery. A compact toolbar offers undo/redo, find-and-replace, playback-time insertion and number checks. Click an unchanged timed word to seek; the current spoken word highlights while editing without moving your caret. Right-click for play/replay, find, copy and timestamp insertion. New text receives no invented alignment. It exports as TXT/JSON without attaching old word timing to changed words.
- Higher concurrency shares the same model and memory pool. It can increase total throughput while reducing each file's speed. Settings never start a second model instance.

### Tune the transcript

Settings includes **Reliable** (1 file, 60-second chunks), **Balanced** (2 files, 120-second chunks) and **Throughput** (4 files, 120-second chunks) presets. They are starting configurations, not measured quality guarantees. Select a preset, adjust it, then save. Defaults apply to new uploads; use **Retry with current settings** to try them on an existing recording.

| Control | Effect |
| :--- | :--- |
| Input language | Fix a known language or allow automatic detection. |
| Output language | Preserve speech or request translation. |
| Temperature | `0` uses greedy decoding; `0–1` allows sampled alternatives. Higher values can reduce consistency. |
| Sampling seed | Optional integer for seeded sampling. Has no effect on greedy decoding. |
| Segment length | 60, 120 or 300 seconds. Shorter segments give faster feedback; longer ones supply more context. |
| Numbers | Ask for conventional digits or natural wording. |
| Speakers | A separate encoder pass identifies speakers across the entire recording; it does not rely on chunk-local slot numbers. |
| Guidance | Editable instructions, with examples for verbatim speech, readable text, names/places, technical vocabulary, amounts/dates and mixed languages. Available per upload too. |

For example: **“Context terms: Nemotron, CUDA, Docker Compose, PostgreSQL. Preserve these spellings when supported by the audio.”** Or: **“Keep fillers, repetitions and false starts. Do not improve grammar.”** Combine compatible guidance and verify important facts against the recording. These controls steer inference; they do not train model weights. Decoder token budgets and expert placement remain deployment settings in `runtime/*.yaml`.

The stock speaker prompt can spell numeric punctuation incorrectly. The digits instruction asks for the actual quantity in conventional notation, including Romanian `10 000 lei`. On the supplied news clip, it produced `10.000 de lei` in both places where the old prompt produced “zece puncte zero zero zero.” This is a targeted regression check, not proof that every amount is correct. **Check numbers** in Corrections finds suspicious phrases for listening and manual review.

Speaker control tags are removed from readable transcripts; raw responses remain available in JSON. One complete-file speaker pass assigns word speakers by real temporal overlap. Existing results with chunk-local IDs remain identified as legacy results until retried. The current runtime limits speaker-enabled recordings to **60 minutes**; longer recordings remain supported with speaker identification disabled.

## Current limits

- Word timing depends on the model’s alignment and can be incomplete. A collapsed alignment that ends substantially before model-detected speech is withheld from highlighting. Changed review words do not inherit old timestamps.
- File-wide speaker identification is limited to 60 minutes by the pinned runtime. Speaker labels are model predictions, not verified identities; overlapping or similar voices can still be confused.
- Translated output may have no usable word alignment or speaker labels.
- Concurrency above one shares a fixed model pool and does not guarantee higher throughput. Default concurrency is one; this deployment uses one worker host and one SQLite data directory.
- Independent-reference Romanian WER/CER validation remains open.

## Sources and licenses

Model: [NVIDIA official checkpoint](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B). Community GGUF pair: [AtomicChat](https://huggingface.co/AtomicChat/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF). Runtime: [NVIDIA/NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp).

Runtime source follows **Apache-2.0**; ggml/llama.cpp and other components retain their notices in the runtime image. Model weights follow **OpenMDW License 1.1**. See [runtime/NOTICE.md](runtime/NOTICE.md) and upstream model terms before redistribution.

<!-- Screenshots are generated by scripts/capture_screenshots.py against the running app. -->
