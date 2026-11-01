# Work tracking and acceptance criteria

This file tracks the requested analyst-workspace expansion. A checkbox is marked only after implementation and relevant verification; a feature being available does not establish model accuracy.

## Requested features

- [x] **1 — Accuracy benchmark lab.** A user can select a completed recording/run, provide an independently reviewed reference, compute normalized WER/CER with substitution/insertion/deletion counts, retain the result, and inspect the settings and measured processing metrics that produced it. Identical model/reference text is flagged for independent-review verification.
- [x] **2 — Edited-text realignment.** Saved full transcripts and selected passages can be queued for genuine CTC alignment against their audio. Surrogate-pair selection offsets, task restarts, unavailable alignment and stale revisions are handled. Successful realignment enables word seeking/highlighting and complete reviewed WebVTT export; timestamps are never interpolated.
- [x] **3 — Interactive waveform.** Waveform peaks derive from actual decoded audio; pointer/keyboard seeking follows the recording, and horizontal dragging selects a bounded audio region without loading the full decoded recording into browser memory.
- [x] **4 — A–B loops.** A waveform region or timed transcript selection can be looped repeatedly and stopped, with start/end validated against the source duration. Switching recordings stops the previous loop.
- [x] **6 — Speaker names.** Names persist per recording and appear consistently in Player, Corrections and speaker-separated exports. Original model responses remain intact.
- [x] **7 — Speaker reassignment.** A timed selection can be assigned to an existing/new speaker; the override persists and updates both transcript views without changing word times.
- [x] **8 — Merge/split speakers.** A user can merge identities or assign a selected range to a new identity. Cycles and invalid ranges are rejected; unrelated ranges are unchanged.
- [x] **9 — Review progress.** Reviewed time ranges persist with revision protection, overlapping intervals count once, and Next unreviewed seeks to the next unfinished interval.
- [x] **14 — Run comparison.** Retries retain prior text, settings and metrics. Separate profile variants preserve the source recording and appear in its comparison picker. Text differences are displayed safely side by side.
- [x] **15 — Named profiles.** Validated profiles can be created, edited, applied, deleted and selected on Upload; applying a profile does not change queue pause state.
- [x] **16 — Vocabulary.** Named lists can be maintained and selected in profiles/defaults. Terms enter model guidance only within the instruction budget; unknown/deleted IDs and unsafe control tokens are rejected.
- [x] **17 — Silence-aware chunks.** Near-pause boundaries retain the original audio timeline and all decoded samples; chunk planning and restart signatures are deterministic.
- [x] **18 — Boundary context/overlap.** Configurable overlap/context improves continuity while exported text and timestamps do not double-count overlapping audio; legacy fixed-chunk checkpoints remain compatible.
- [x] **19 — Separate channels.** Multi-channel recordings can be transcribed independently. Output identifies channel provenance, preserves each channel's original time offsets, and does not double-count recording duration.
- [x] **21 — Bulk operations.** Files supports selection across pages and batch retry, pause/resume/cancel, archive, organize, delete and export. Partial failures identify affected files; active-file deletion remains protected.
- [x] **23 — Individual processing control.** Pause/cancel preserves source files and checkpoints, stops further admission for the recording at a safe boundary, and resume reuses completed chunks. Queue-wide pause remains independent.
- [x] **24 — Queue ETA.** Queued recordings show estimated start/finish and a queue completion estimate based on observed processing, concurrency and active work. Offline/paused queues and insufficient observations do not show fabricated ETAs.
- [x] **25 — Resumable upload.** Uploads persist verified offsets, resume after interruption/reload, reject incorrect offsets/oversized chunks and finalize idempotently. A lost response after the final chunk does not require re-uploading.
- [x] **27 — Projects/labels/archive.** Files supports project, label and archive filters with server pagination. Organization persists; archived recordings retain media, review and exports.
- [x] **30 — Rich exports.** Original/review/selection TXT, JSON, DOCX and PDF preserve Romanian text and escape markup. WebVTT requires complete genuine timing. Bulk ZIP includes explicit per-recording failures; speaker-separated text preserves unaligned content.
- [x] **Selection-aware contextual menu.** Drag-selecting multiple words exposes passage actions: play, loop, export text/audio/documents, assign/split speaker, mark reviewed and realign. Missing timing disables audio-dependent actions with an explanation; keyboard context-menu access remains available.

## Additional requested feature

- [ ] **AI correction explanation.** A word/passage context action collects selected text, analyst-corrected wording and the exact audio interval, queues a local-model explanation, persists its history and errors, and keeps historical requests tied to the original selection. Untimed text requires an explicit valid waveform interval. The UI must not imply automatic weight training.

Evidence for completed features: 115 Python tests; seven-route desktop/mobile checks; 17 real-browser review interaction checks; live workspace API/UI checks; real 117-second silence/overlap, stereo, forced-alignment and individual pause/resume/cancel checks. Raw recordings/transcripts stay in ignored local evidence.

## Release checks for this expansion

- [x] Run the complete Python test suite and relevant desktop/mobile browser workflows; record real-model alignment, advanced chunking/channel and pause/resume proof.
- [x] Verify the complete Docker deployment starts and serves the updated UI, backend, worker, monitor and pinned runtime.
- [ ] Refresh README feature/limitation descriptions and screenshots from every page, including selection menu and new workspace tools.
- [ ] Commit/push reviewed milestones and verify the latest GitHub Actions run.

## Further validation and limitations

- [ ] **Independent Romanian accuracy evaluation.** Acceptance: human-reviewed references for representative news, conversation, names, numeric amounts, noise and overlapping speech; publish WER/CER with reference provenance and normalization. Do not treat model output as reference.
- [ ] **Controlled performance matrix.** Acceptance: repeat comparable warm/cold workloads at concurrency 1/2/4/8/16 and chunk settings, publish throughput, per-file latency and peak RAM/VRAM with workload/configuration details.
- [ ] **Longer speaker-enabled recordings.** Acceptance: stable validated identities on multi-hour audio across processing windows, with explicit handling of new/overlapping speakers and measured memory bounds. The current 60-minute runtime limit must remain visible until this passes.
- [ ] **Extended fault-injection soak.** Acceptance: model/worker hard kills, disk exhaustion, repeated restarts and interrupted uploads preserve original media, reviews and compatible completed chunks without duplicate jobs or silent data loss.
- [ ] **Independent fresh-host installation and restore.** Acceptance: clone and Compose startup on a second supported machine; restore database, recordings, results and reviews from a documented complete backup.
- [ ] **Portable browser CI fixtures.** Acceptance: browser interaction tests run in CI using redistributable/synthetic fixtures without private local recordings; model-dependent checks are separated and clearly labeled.
