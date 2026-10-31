# Local transcription implementation plan

> **For agentic workers:** Execute inline with the executing-plans workflow. The user's existing phased plan in docs/03-implementation-plan.md controls CPU-before-hybrid ordering.

**Goal:** Deliver a durable local upload UI, a reproducible Nemotron CPU baseline, measured RTX 3080 expert offload if the CPU prerequisite succeeds, and inspectable benchmark evidence.

**Architecture:** Keep one NeMo HTTP model server and one file worker. SQLite stores jobs, bind mounts store inputs/exports, and FFmpeg produces bounded PCM chunks. Hybrid runtime changes stay in an external checkout until reviewed and validated.

**Tech Stack:** Docker Compose, pinned NVIDIA NeMo-Speech.cpp/llama.cpp, Python/FastAPI, SQLite, FFmpeg, plain browser HTML/CSS/JS.

## Task 1 — Establish reproducible provisioning

Files: scripts/bootstrap.sh, scripts/download_model.py, runtime.lock.json, compose.yaml, .env.example, pytest.ini.

- [x] Read README and all eight numbered docs; inspect original app, runtime and tests.
- [x] Detect host RAM/VRAM/disk and record original Compose/test results.
- [ ] Pin upstream/model revisions and exact model SHA256/byte sizes; verify downloaded pair without fetching weights implicitly.
- [ ] Initialize all submodules the upstream Dockerfile copies licenses from; build CPU runtime and check its actual help.
- [ ] Test commands: `docker compose config`, `PYTHONPATH=app .venv/bin/pytest tests -q`, `docker compose build nemo web worker`.

## Task 2 — Exercise CPU prerequisite

Files: scripts/benchmark.py, evidence/cpu-*, docs/09-validation.md.

- [ ] Obtain user-invoked model download and a Romanian WAV/reference; use synthetic speech only with explicit evidence labeling.
- [ ] Start server, verify readiness/model list, transcribe three sequential requests; capture runtime logs, RTFx, process RSS and GPU allocation.
- [ ] Run benchmark with `python scripts/benchmark.py --help`; preserve raw responses and normalization/error counts for WER/CER.

## Task 3 — Complete upload/queue/export workflow

Files: app/main.py, app/db.py, app/media.py, app/worker.py, app/static/*, tests/*.

- [ ] Validate decodable audio, enforce configured size/duration/disk limits, restrict local API mutations to same origin.
- [ ] Enforce one host worker with an OS lock before recovery, reset recovery counters, support deleting finished/queued jobs and bounded retention.
- [ ] Preserve measured sample offsets, raw model replies and timings; omit SRT for any invalid alignment, never substitute duration guesses.
- [ ] Fail OOM without retries, bound other retries, and prevent uncertain network timeout retries from duplicating in-flight inference.
- [ ] Build an accessible local studio UI with upload progress, queue/results preview and measured speed; verify desktop/mobile in a browser.
- [ ] Add regression tests for malformed alignment, content rejection, restart/locking, deletion, transient/permanent/OOM responses, real FFmpeg and a synthetic one-hour MP4 with mocked inference.

## Task 4 — Validate hybrid runtime after CPU succeeds

Files: external runtime checkout; repository launcher/config only after compatible patch is tested.

- [ ] Locate auditable maintained patch or implement CPU expert tensor overrides for the exact llama.cpp MoE/Mamba path.
- [ ] Record upstream/patch revisions, keep tensor placement and mmap semantics explicit, capability-check the binary.
- [ ] Build sm_86 CUDA, compare identical Romanian input against CPU; capture VRAM <=9 GiB, peak RSS, speed, transcript error rates and OOM recovery.
- [ ] Integrate a single mode switch and retain tested CPU fallback; leave gates open if prerequisite or evidence is missing.

## Task 5 — Evidence and handoff

Files: README.md, docs/03-implementation-plan.md, docs/04-acceptance-criteria.md, docs/09-validation.md, evidence/*.

- [ ] Run relevant unit/integration/browser/Compose checks, separate simulated backend tests from real model measurements.
- [ ] Document exact launch/benchmark commands, source revisions, measured performance and unresolved acceptance gates.
- [ ] User subsequently requested linking the empty GitHub repository, initializing main, committing and pushing. Preserve runtime/model provenance separately from application history.
