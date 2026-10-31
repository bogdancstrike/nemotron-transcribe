#!/usr/bin/env python3
"""Submit local files sequentially through the UI API and capture real host measurements.

Run on the Docker host with httpx and psutil installed. References are optional:
without an independent reference, WER/CER remain null. This script never downloads models.
"""
import argparse
import csv
import hashlib
import json
import math
import statistics
import subprocess
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]


def normalize(text):
    text = unicodedata.normalize('NFC', text).casefold()
    return ' '.join(''.join(c if c.isalnum() or c.isspace() else ' ' for c in text).split())


def error_rate(reference, hypothesis):
    """Levenshtein edit counts with deterministic substitution/deletion/insertion ties."""
    previous = [(i, 0, i, 0) for i in range(len(hypothesis) + 1)]
    for r, ref in enumerate(reference, 1):
        current = [(r, 0, 0, r)]
        for h, hyp in enumerate(hypothesis, 1):
            diagonal = previous[h - 1]
            if ref == hyp:
                current.append(diagonal)
            else:
                substitution = (diagonal[0] + 1, diagonal[1] + 1, diagonal[2], diagonal[3])
                insertion = (current[-1][0] + 1, current[-1][1], current[-1][2] + 1, current[-1][3])
                deletion = (previous[h][0] + 1, previous[h][1], previous[h][2], previous[h][3] + 1)
                current.append(min((substitution, insertion, deletion), key=lambda x: x[0]))
        previous = current
    edits, substitutions, insertions, deletions = previous[-1]
    return {'rate': edits / len(reference) if reference else None,
            'reference_length':len(reference), 'edits':edits,
            'substitutions':substitutions,'insertions':insertions,'deletions':deletions}


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * p
    lo = math.floor(index)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)


class Monitor:
    def __init__(self, directory, pid):
        self.directory, self.pid = directory, pid
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.peak_rss_mib, self.peak_gpu_mib = None, None
        self.peak_engine_gpu_mib, self.peak_gpu_util = None, None

    def run(self):
        with (self.directory / 'resources.csv').open('w', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow(['utc','process_rss_mib','process_cpu_percent','host_available_mib','gpu_used_mib','gpu_util_percent','engine_gpu_mib'])
            process = psutil.Process(self.pid) if self.pid else None
            if process:
                process.cpu_percent()
            while not self.stop.is_set():
                rss = cpu = gpu = util = engine_gpu = None
                if process:
                    try:
                        rss, cpu = process.memory_info().rss / 1024**2, process.cpu_percent()
                        self.peak_rss_mib = max(self.peak_rss_mib or 0, rss)
                    except psutil.Error:
                        pass
                try:
                    output = subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,utilization.gpu','--format=csv,noheader,nounits'], text=True, timeout=4)
                    gpu, util = map(float, output.strip().splitlines()[0].split(','))
                    self.peak_gpu_mib, self.peak_gpu_util = max(self.peak_gpu_mib or 0, gpu), max(self.peak_gpu_util or 0, util)
                    compute = subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits'], text=True, timeout=4)
                    engine_gpu = 0.0
                    for row in csv.reader(compute.splitlines()):
                        if len(row) == 2 and row[0].strip() == str(self.pid):
                            engine_gpu += float(row[1])
                    self.peak_engine_gpu_mib = max(self.peak_engine_gpu_mib or 0, engine_gpu)
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
                writer.writerow([datetime.now(timezone.utc).isoformat(),rss,cpu,psutil.virtual_memory().available / 1024**2,gpu,util,engine_gpu])
                fh.flush()
                self.stop.wait(0.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=12)


def engine_pid():
    container = subprocess.check_output(['docker','compose','ps','-q','nemo'], cwd=ROOT, text=True).strip()
    if not container:
        raise RuntimeError('Compose nemo service is not running')
    return int(subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',container], text=True).strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--base-url', default='http://127.0.0.1:7860')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--segment-seconds', type=int, choices=(60,120,300), default=60)
    parser.add_argument('--deadline-seconds', type=int, default=14400)
    parser.add_argument('--reference', type=Path, help='Independent UTF-8 transcript; only allowed with one input file')
    args = parser.parse_args()
    if args.reference and len(args.files) != 1:
        parser.error('--reference requires exactly one input file')
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    args.output.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / 'runtime.lock.json').read_text())
    rows = []
    with httpx.Client(base_url=args.base_url, timeout=300) as client:
        readiness = client.get('/api/system').raise_for_status().json()
        if not readiness.get('engine_ready'):
            raise SystemExit('Model not ready; wait for the engine before benchmarking')
        (args.output / 'system.json').write_text(json.dumps(readiness, indent=2))
        (args.output / 'free.txt').write_text(subprocess.check_output(['free','-h'], text=True))
        with Monitor(args.output, engine_pid()) as monitor:
            for source in args.files:
                source = source.resolve(strict=True)
                checksum = hashlib.file_digest(source.open('rb'), 'sha256').hexdigest()
                for repeat in range(args.repeats):
                    start = time.perf_counter()
                    with source.open('rb') as stream:
                        created = client.post('/api/jobs', files={'file':(source.name,stream,'application/octet-stream')},
                                              data={'language':'ro','segment_seconds':str(args.segment_seconds)}).raise_for_status().json()
                    id, deadline = created['id'], time.monotonic() + args.deadline_seconds
                    print(f'Submitted {source.name} repeat {repeat+1}: {id}', flush=True)
                    while time.monotonic() < deadline:
                        status = client.get('/api/jobs/' + id).raise_for_status().json()
                        if status['status'] in {'done','failed'}:
                            break
                        time.sleep(2)
                    else:
                        raise TimeoutError(f'Job {id} exceeded benchmark deadline; it is still queued/running')
                    row = {'file':str(source),'sha256':checksum,'repeat':repeat+1,'job_id':id,
                           'backend':readiness['backend'],'runtime_commit':lock['upstream_commit'],
                           'model_revision':lock['model_revision'],'segment_seconds':args.segment_seconds,
                           'upload_queue_job_wall_s':time.perf_counter()-start,'status':status['status'],
                           'error':status.get('error'),'wer':None,'cer':None}
                    if status['status'] == 'done':
                        result = client.get(f'/api/jobs/{id}/download/json').raise_for_status().json()
                        (args.output / f'{id}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
                        latencies = [s['inference_s'] for s in result['segments']]
                        row.update({k:result[k] for k in ('duration_s','wall_s','inference_s','rtfx','inference_rtfx','srt_available')})
                        row.update({'rtf':result['wall_s']/result['duration_s'],
                                    'segment_p50_s':percentile(latencies,.5),'segment_p95_s':percentile(latencies,.95),
                                    'text':result['text']})
                        if args.reference:
                            ref, hyp = normalize(args.reference.read_text(encoding='utf-8')), normalize(result['text'])
                            row['word_errors'], row['character_errors'] = error_rate(ref.split(),hyp.split()), error_rate(list(ref),list(hyp))
                            row['wer'], row['cer'] = row['word_errors']['rate'], row['character_errors']['rate']
                    rows.append(row)
                    atomic = args.output / 'runs.json.tmp'
                    atomic.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                    atomic.replace(args.output / 'runs.json')
                    print(json.dumps(row, ensure_ascii=False), flush=True)
        summary = {'runs':len(rows),'failures':sum(r['status']=='failed' for r in rows),
                   'peak_process_rss_mib':monitor.peak_rss_mib,'peak_gpu_total_mib':monitor.peak_gpu_mib,
                   'peak_engine_gpu_mib':monitor.peak_engine_gpu_mib,'peak_gpu_util_percent':monitor.peak_gpu_util,
                   'wer_normalization':'NFC, casefold, punctuation to spaces; diacritics preserved',
                   'quality_reference_available':bool(args.reference),
                   'median_rtfx':statistics.median([r['rtfx'] for r in rows if 'rtfx' in r]) if any('rtfx' in r for r in rows) else None}
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
        fields = sorted(set().union(*(r.keys() for r in rows)) - {'text','word_errors','character_errors'}) if rows else []
        with (args.output / 'benchmarks.csv').open('w',newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
