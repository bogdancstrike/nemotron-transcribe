# Implementation plan — staged agent work

Every phase must produce working artifacts, test reports, measured limitations, and a commit. Do not mark the GPU hybrid phase complete based only on Docker Compose accepting `gpus: all`.

## Phase 0 — Pin and validate upstream (~P0)

1. Read current NVIDIA/NeMo-Speech.cpp `docs/salm`, `docs/api.md`, `docs/build.md`, `docker/Dockerfile`.
2. Pin an upstream **commit SHA** and record it in `.env` / docs; avoid deploying a moving `main` for a reproducible build.
3. Run `bash scripts/bootstrap.sh`, downloading submodules. Inspect `docker compose config` and `docker compose --profile setup config`. Resolve any option drift.
4. Use HF CLI or provided downloader to download only `AD-IQ4_NL.gguf` + matching `Encoder-F16.gguf` and check sizes/hashes.
5. Verify model license acceptance and downloaded files; no model weights in repository.

**Exit:** reproducible pinned build logs, exact file sizes, known model hash, no missing encoder.

## Phase 1 — CPU/RAM proof of concept (~P0)

1. Build official `docker/Dockerfile --target runtime` with `ENABLE_CUDA=OFF`, `ENABLE_SPEECHLM=ON`, `ENABLE_HTTP=ON`; adjust build args if upstream changes.
2. Start CPU-only SALM server once with `max_sequences=1`, `n_ctx_per_sequence=16384`, `max_context=16384`, `mtp_draft=0`, bounded `n_batch`, 8–16 threads.
3. Test `/ready`, `GET /v1/models`, and `POST /v1/audio/transcriptions` with an actual Romanian 30–90s PCM WAV. Record latency and correctness.
4. Ensure no OOM and that the process remains reusable across >3 sequential requests without reloading weights.

**Exit:** real transcript using model from RAM, stable CPU RSS and no inadvertent CUDA allocation.

## Phase 2 — UI and file workflow (~P0)

1. FastAPI endpoints and upload limit; content validation by FFprobe; ensure upload does not buffer entire file in memory.
2. Durable FIFO queue, single active worker, restart recovery; tests for concurrent job creation and invalid files.
3. FFmpeg 16kHz mono PCM conversion and chunking; verify 1-hour MP4 and audio-only files.
4. POST each chunk to NeMo with `verbose_json`, Romanian language hint; extract aligned words; retain raw model response and timestamps.
5. Merge text, translate chunk-relative timestamp offsets, produce TXT/JSON/SRT **only if precise timestamps**. Preserve diacritics.
6. Frontend shows upload, progress, error status and download links. Accessibility/keyboard usage and mobile viewport.
7. Add upload/job cleanup and configurable retention for shared machines.

**Exit:** browser upload through completed outputs with a real 1h test, progress stable, no job loss on web restart.

## Phase 3 — RTX 3080 hybrid GPU/CPU experts (~R&D P0, not yet implemented)

A community report of `--asr.salm.n_cpu_moe 40` pertains to **a custom fork of NeMo-Speech.cpp**. The stock server does not officially provide that option. Therefore this is a code/runtime change, not a Compose tweak.

1. Find patch **source code and revision**, or implement the missing hybrid MoE expert placement in a separate reproducible fork. Verify license and commit.
2. Confirm execution semantics with SALM's hybrid Mamba-2 + Transformer MoE; load selected expert layers in CPU DRAM and the remaining available tensors on GPU. Document which tensors remain on GPU (encoder, attention/Mamba, experts, KV, etc.).
3. Target `sm_86` RTX 3080 CUDA; compile with NVIDIA container toolkit and appropriate CUDA version/driver. Verify `nvidia-smi` shows your card in the container.
4. Add a **runtime capability check**: `nemo-speech help serve` / config schema must recognize `asr.salm.n_cpu_moe` (or patch equivalent); startup fails with clear message if it does not.
5. Load GGUF IQ4_NL and encoder, 64 GB RAM; keep initial GPU ceiling <= 9.0 GiB and monitor peaks. Select experiment values 48/40/32 CPU MoE layers only after the fork documents their precise meaning; never assume a fixed layer-count range.
6. Run short Romanian samples. Compare transcript quality against CPU baseline and measure speedup/slowdown. Ensure CPU fallback switch is one `.env`/Compose choice.
7. Test failure and recovery when VRAM exhausted; no silent fallback or corrupted transcript.

**Exit:** verified RTX 3080 GPU utilization during decoding, CPU DRAM expert residency, VRAM <10 GB with margin, same/similar Romanian transcript quality, measured throughput and no crashes on at least three successive files. If infeasible, record blocker and recommend Whisper Large v3 rather than promise a GPU feature.

## Phase 4 — Hardening and packaging (~P1)

- Unit tests, API integration tests (mock NeMo), one real model smoke test, long-audio integration test, GPU offload benchmark.
- Docker healthchecks / better `/ready` handling; compose startup docs; stop/memory behavior.
- Input validation, disable public binding, resource limits and data retention; run web/worker non-root.
- Publish reproducible Docker build, SHA-pinned assets, license notices and results of acceptance cases.

## Phase 5 — Optional features (post MVP)

- Context prompts/named entities using SALM `/v1/chat/completions` (requires base64 audio and changed API response).
- Better VAD-aware chunk boundaries + overlap/deduplication to avoid words cut mid-syllable.
- Speaker-ID reconciliation between chunks, subtitles with overlap-aware cues.
- Queue workers backed by Redis for a multi-GPU production deployment; Keycloak only if exposed to users.
