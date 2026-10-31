"""Bounded local media inspection. No network protocols or shell filename expansion."""
import json
import math
import os
import subprocess
from pathlib import Path

MAX_DURATION = float(os.getenv('MAX_DURATION_SECONDS', '14400'))


def probe_audio(path: str | Path) -> float:
    try:
        proc = subprocess.run(
            ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
             '-select_streams', 'a:0', '-show_entries', 'stream=codec_type:format=duration',
             '-of', 'json', str(path)], capture_output=True, timeout=30, check=False)
        result = json.loads(proc.stdout) if proc.returncode == 0 else {}
        duration = float(result.get('format', {}).get('duration', 0))
        if not result.get('streams') or not math.isfinite(duration) or duration <= 0:
            raise ValueError('File must contain a decodable audio stream with a known duration')
        if duration > MAX_DURATION:
            raise ValueError(f'Audio exceeds the {MAX_DURATION:g} second duration limit')
        return duration
    except (subprocess.TimeoutExpired, json.JSONDecodeError, TypeError) as exc:
        raise ValueError('Unable to inspect audio within the media limits') from exc
