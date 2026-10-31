# Benchmark plan for Romanian STT on CPU and RTX 3080 hybrid

## Test corpus

Prepare consented ground-truth WAV files in Romanian: 5x 30–60s news clips, 5x 60–120s conversational clips, 5x 30–120s clips with proper nouns, 3x noisy/overlapped speech; plus two 1-hour broadcasts for soak testing. Hold sample rate and transcript normalization constant. Include short English/Romanian mixed speech if relevant.

Baseline models: Nemotron 3.5 Transcribe IQ4_NL on CPU only; optional patched hybrid; Whisper Large v3 on the same RTX 3080 with faster-whisper for a practical speed/accuracy comparator.

## Record these per clip

`model`, `runtime_commit`, `model_quant`, `backend`, `n_cpu_moe`, `segment_seconds`, `duration_s`, `wall_s`, `rtfx`, `peak_gpu_mib`, `peak_ram_mib`, `cpu_util`, `gpu_util`, `total_words`, `wer`, `cer`, `named_entity_recall`, `word_timestamp_mae_ms`, `error`.

RTFx formula: `audio_seconds / inference_wall_seconds`. Real-time capability requires RTFx >= 1 (excluding startup if measuring warm execution; also report cold startup separately). Overall job end-to-end RTFx includes FFmpeg and all segments.

## Procedure

1. `nvidia-smi --query-gpu=timestamp,memory.used,utilization.gpu --format=csv -lms 500 > evidence/nvidia-smi.csv` while processing hybrid clips.
2. Record peak per-process RSS (e.g. `/usr/bin/time -v ...` or process memory monitor) and host `free -h`.
3. Warm the backend once; process every sample 3 times, retain median and p95 latency; also record cold model load.
4. Compare Romanian WER with identical normalization (diacritics policy explicit, punctuation/case normalization fixed) and named-entity metrics; log deletions/insertions/substitutions.
5. Conduct 1-hour soak and a batch of 5 uploads, verify queue, progress and idempotence.
6. Archive `evidence/` logs, markdown summary, CPU/hybrid comparison, confidence caveats. Do not publish guessed performance figures.

## Go/no-go

- If hybrid is slower than Whisper Large v3 while providing no meaningful accuracy improvement on local corpus, prefer Whisper for throughput workloads.
- If model exceeds available VRAM or RAM, reduce GPU layers/context/batch after reviewing runtime placement; do not disable OOM detection or system safeguards.
- If Romanian WER worsens materially after quantization, compare Q5 or CPU higher precision before claiming quantization equivalence.
