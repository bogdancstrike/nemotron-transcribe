#!/usr/bin/env python3
"""Resumable full-file benchmark; poll safely across UI restarts without duplicate uploads."""
import argparse
import json
import subprocess
import time
from pathlib import Path

import httpx

from benchmark import Monitor,engine_pid,percentile


def get(client,path):
    for attempt in range(30):
        try:
            response=client.get(path)
            response.raise_for_status()
            return response.json()
        except (httpx.RequestError,httpx.HTTPStatusError):
            if attempt==29:raise
            time.sleep(2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files',nargs='+',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--adopt',action='append',default=[],help='filename=existing_job_id')
    parser.add_argument('--segment-seconds',type=int,default=60,choices=(60,120,300))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    state_path=args.output/'batch.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    adopted=dict(pair.split('=',1) for pair in args.adopt)
    with httpx.Client(base_url='http://127.0.0.1:7860',timeout=300) as client:
        for path in args.files:
            path=path.resolve(strict=True)
            if path.name in state:continue
            if path.name in adopted:
                id=adopted[path.name]
            else:
                with path.open('rb') as file:
                    id=client.post('/api/jobs',files={'file':(path.name,file,'audio/mp4')},data={'language':'ro','output_language':'source','diarize':'false','segment_seconds':args.segment_seconds}).raise_for_status().json()['id']
            state[path.name]={'file':str(path),'job_id':id,'status':'pending'}
            state_path.write_text(json.dumps(state,indent=2))
            print(f'{path.name}: {id}',flush=True)
        with Monitor(args.output,engine_pid()) as monitor:
            while True:
                pending=False
                for name,row in state.items():
                    if row['status'] in {'done','failed'}:continue
                    job=get(client,'/api/jobs/'+row['job_id'])
                    if job['status'] not in {'done','failed'}:
                        pending=True;continue
                    row.update({key:job.get(key) for key in ('status','duration_s','wall_s','inference_s','rtfx','error')})
                    if job['status']=='done':
                        result=get(client,f'/api/jobs/{row["job_id"]}/download/json')
                        (args.output/(row['job_id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
                        latencies=[segment['inference_s'] for segment in result['segments']]
                        row.update({'segment_p50_s':percentile(latencies,.5),'segment_p95_s':percentile(latencies,.95),
                                    'srt_available':result['srt_available'],'wer':None,'cer':None})
                    state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2))
                    print(json.dumps(row,ensure_ascii=False),flush=True)
                if not pending:break
                time.sleep(3)
        (args.output/'summary.json').write_text(json.dumps({'files':len(state),'failures':sum(r['status']=='failed' for r in state.values()),
            'peak_rss_mib':monitor.peak_rss_mib,'peak_gpu_total_mib':monitor.peak_gpu_mib,
            'peak_engine_gpu_mib':monitor.peak_engine_gpu_mib,'note':'Includes operator-selected concurrency and restart validation; see per-file reports.'},indent=2))


if __name__=='__main__':main()
