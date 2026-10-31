# Primary sources checked 2026-10-31

- NVIDIA official model / license / language matrix: https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B
- AtomicChat prequantized GGUF filenames & measurements: https://huggingface.co/AtomicChat/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF
- NeMo-Speech.cpp official repo: https://github.com/NVIDIA/NeMo-Speech.cpp
- Native C++ runtime Docker build instructions: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/build.md
- Official container build specification: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docker/Dockerfile
- SALM reference: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/salm/README.md
- SALM resource configuration: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/salm/configuration.md
- Local server flags: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/server.md
- OpenAI-compatible transcription HTTP subset: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/api.md
- Experimental `n_cpu_moe` claim (community report; NOT official): https://teapawt.net/DogukanUrker

The community claim is only evidence that *someone* ran a patched build, not an open, audited reproducible patch. The handoff deliberately does not invent a patch URL, assume upstream support, or claim hybrid benchmarks on the target RTX 3080.
