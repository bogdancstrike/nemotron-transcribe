#!/usr/bin/env bash
set -euo pipefail
# Real model end-to-end check; requires running docker compose and known-good Romanian WAV.
WAV="${1:?Usage: scripts/smoke_cpu.sh path/to/romanian_sample.wav}"
if ! [[ -f "$WAV" ]]; then echo "No file at $WAV" >&2; exit 1; fi
BASE="${WEB_BASE_URL:-http://127.0.0.1:7860}"
curl -fSs "$BASE/api/system" && echo
JOB_JSON="$(curl -fSs "$BASE/api/jobs" -F "file=@${WAV}" -F language=ro -F segment_seconds=60)"
printf '%s\n' "$JOB_JSON"
JOB_ID="$(printf '%s' "$JOB_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
echo "Job created: $JOB_ID — poll $BASE/api/jobs/$JOB_ID for completion"
echo "The job may be very slow on CPU. No inference throughput is assumed."
