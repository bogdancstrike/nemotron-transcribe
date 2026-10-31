#!/usr/bin/env python3
"""Explicit opt-in, pinned model download and integrity check. Importing never downloads."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(path, expected):
    if not path.is_file() or path.stat().st_size != expected['size']:
        raise ValueError(f"Missing or wrong size: {path} (expected {expected['size']} bytes)")
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected['sha256']:
        raise ValueError(f'SHA256 mismatch: {path}')
    return {'filename': path.name, 'size': path.stat().st_size, 'sha256': digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, default=Path(os.getenv('MODELS_DIR', ROOT / 'models')))
    parser.add_argument('--verify-only', action='store_true', help='Never access the network')
    args = parser.parse_args()
    lock = json.loads((ROOT / 'runtime.lock.json').read_text())
    args.models_dir.mkdir(parents=True, exist_ok=True)
    if os.getenv('MODEL_QUANT', 'AD-IQ4_NL') != 'AD-IQ4_NL':
        parser.error('This release locks the documented AD-IQ4_NL + Encoder-F16 pair')
    complete = all((args.models_dir / f['filename']).is_file() and (args.models_dir / f['filename']).stat().st_size == f['size'] for f in lock['files'])
    if not args.verify_only and not complete:
        required = sum(f['size'] for f in lock['files'] if not (args.models_dir / f['filename']).exists())
        if shutil.disk_usage(args.models_dir).free < required + 2 * 1024**3:
            parser.error('Insufficient free space for the pair plus 2 GiB reserve')
        from huggingface_hub import hf_hub_download
        for expected in lock['files']:
            print(f"Fetching {expected['filename']} at {lock['model_revision']}", flush=True)
            hf_hub_download(repo_id=lock['model_repo'], filename=expected['filename'],
                            revision=lock['model_revision'], local_dir=args.models_dir,
                            token=os.getenv('HF_TOKEN') or None)
    verified = [verify(args.models_dir / f['filename'], f) for f in lock['files']]
    manifest = {**lock, 'files': verified}
    temporary = args.models_dir / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(args.models_dir / 'manifest.json')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
