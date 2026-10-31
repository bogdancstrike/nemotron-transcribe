# Copy-paste implementation brief for an agentic coding tool

Implement and validate the project in this repository for my workstation (Ubuntu, Ryzen 9 9950X3D, 64 GB RAM, RTX 3080 10 GB). I want a local browser UI where I upload video/audio and get Romanian transcripts, timestamped SRT if alignment is available, and TXT/JSON.

**Read** `AGENTS.md`, all files under `docs/`, and `README.md` before starting. Work through `tasks.yaml` in dependency order, produce source code and tests, and update the acceptance criteria with actual evidence.

The selected model is **NVIDIA Nemotron 3.5 Transcribe 30B-A4B, IQ4_NL GGUF** from Hugging Face `AtomicChat/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF` (mandatory encoder + decoder). NVIDIA official model is `nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B`. Runtime must be NVIDIA NeMo-Speech.cpp, not a different ASR model marketed as Nemotron.

**Critical**: baseline code supports CPU execution. The 10 GB GPU cannot hold the model; GPU+CPU expert offload needs a **real, reviewable compatible patch**, not just `gpus: all`, and must be tested. Don't silently claim this milestone is finished. Verify and record model SHA, runtime commit, GPU VRAM, system RAM, and measured inference speeds. If patch cannot be found or made safe, keep CPU implementation functional and state precisely what blocks hybrid GPU support.

Priority: (1) reproducible build/CPU transcription, (2) UI/upload/job stability, (3) RTX 3080 hybrid offloading proof, (4) quality/performance benchmarks, (5) security and cleanup. Define success strictly by `docs/04-acceptance-criteria.md`.

Run unit tests, API tests, Docker Compose config and end-to-end Romanian tests on the actual machine. Never fabricate successful GPU benchmarks. Use existing code as a scaffold, not a validated production implementation. Provide a final summary with exact files changed, tested commands, logs/artifact locations, remaining risks and blockers.
