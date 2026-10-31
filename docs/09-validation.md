# Validation log — 2026-10-31

This file distinguishes tests of application logic from real model/hardware work.

- Original six application tests passed. Running unconstrained pytest after cloning vendor also collected upstream conversion tests; pytest.ini now limits collection to this application.
- Original and revised `docker compose config` validate.
- Official CPU runtime built from commit `20999cc921d2b14aa58708f61756a8b2e787ba20`, llama.cpp `bd4f514db14d87fded667787a7a963bfbaa98e89`.
- Both exact model files downloaded and verified against pinned byte counts and SHA-256 in runtime.lock.json.
- CPU `/ready` reports ready; `/v1/models` reports SALM transcription, word timestamps and diarization capabilities.
- Three sequential real Romanian 60-second WAV runs succeeded, retained diacritics and aligned SRT, with no model reload. Median RTFx 2.8586; peak RSS 35,589.44 MiB; model GPU allocation 0 MiB. Desktop GPU usage is separately reported and is not inference usage.
- Five real 60-second M4A excerpts from the requested filenames succeeded; median RTFx 3.1589. Full recordings were not used for these five measurements.
- 35 application tests passed, including a synthetic one-hour MP4 with real FFmpeg and simulated inference. Byte-range serving and malformed/partial alignment are covered.
- Chromium checks passed for five tabs, a wide centered upload chooser, M4A-first selection, real recorded-media decoding and model-timestamp word highlighting, automatic navigation/focus after upload, and every tab at 390px without horizontal overflow.
- First experimental hybrid build attempted full-GPU decoder allocation because CPU expert overrides were nested in a disabled fused-path branch. Startup failed with an attempted 17,658 MiB CUDA allocation. The failure is captured locally; the patch was corrected and hardware validation is ongoing.
- No independent Romanian reference transcript exists yet, so WER/CER and quantization-quality acceptance remain unverified.

Commands and raw outputs are retained locally in `evidence/` (not published because they contain user media/transcripts). README records only aggregate measurements. Acceptance items are not all signed off.
