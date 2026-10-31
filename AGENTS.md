# Agent execution contract — Nemotron local transcription

You are implementing a **local, single-host**, Docker Compose-based transcription system on Ubuntu with RTX 3080 10 GB, 64 GB RAM, Ryzen 9950X3D. The user wants the real Nemotron 3.5 Transcribe 30B-A4B in GGUF IQ4_NL with **CPU+RAM offload of experts from GPU**, plus browser file uploads and outputs.

## Operating instructions

1. Read README and `docs/*.md` **before changes**. Treat acceptance criteria as the release contract.
2. First make CPU baseline reproducible and test end-to-end on a short Romanian WAV. Do not skip this prerequisite when tackling hybrid GPU support.
3. **Do not invent GPU offload flags.** Stock NeMo-Speech.cpp does not expose verified `asr.salm.n_cpu_moe`. Locate a maintained compatible patch or implement and test carefully against the upstream commit (including Mamba/Transformer MoE specifics). Record upstream and patch commit SHAs. Keep patched code outside this repository unless license-reviewed.
4. Measure actual GPU allocations (`nvidia-smi`), RAM (`free -h`, peak process RSS), speed (audio duration / wall time), correctness (CER/WER), and OOM recovery.
5. Use the EXACT Hugging Face decoder/encoder filename pairs from `docs/05-model-runtime.md`. Never drop the required encoder. Downloads must be user-invoked; never put model weights in Git/ZIP.
6. Maintain one effective inference engine / model instance and bounded job concurrency. Do not load a new 21 GB model for every segment.
7. Preserve Romanian diacritics (UTF-8). Never synthesize word timestamps if alignment fails: omit SRT or explicitly mark coarse timing in the metadata.
8. Keep interface bound to 127.0.0.1; do not expose inference port. Do not implement authentication as a cosmetic checkbox: public access requires actual auth/reverse proxy design.
9. Execute the tests and record commands/results. Distinguish code-only tests from real GPU/model validation.
10. Update docs with resolved issues, exact compatible commit, known performance, remaining risks; do not mark an acceptance criterion passed without evidence.

## Work sequence

- P0: inspect and test original Compose, dependencies, model download and startup.
- P1: test API/UI/worker, ffmpeg, valid timestamps, restart recovery, 1-hour video behavior.
- P2: CPU transcription Romanian 1-minute clip. Record RTFx.
- P3: hybrid GPU patch proof of concept on 3080 10 GB with model memory mapped and MoE experts in RAM. Capture VRAM/RAM and error logs.
- P4: full integration, fallback to CPU mode, performance evaluation, acceptance signoff.

See `docs/03-implementation-plan.md` for definitions of done and rollback behavior.
