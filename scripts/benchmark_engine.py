#!/usr/bin/env python3
"""Direct engine proof while the queue worker is stopped; API remains internal to Compose."""
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from benchmark import Monitor, engine_pid

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    # No concurrent model submissions: assert the Compose worker is stopped.
    workers=subprocess.check_output(['docker','compose','ps','--status','running','-q','worker'],cwd=ROOT,text=True).strip()
    if workers:
        raise SystemExit('Stop the queue worker before direct engine validation')
    scratch=ROOT/'data'/'engine-validation'
    scratch.mkdir(parents=True,exist_ok=True)
    source=scratch/args.file.name
    shutil.copyfile(args.file,source)
    script='''import httpx,json,time
with open("/data/engine-validation/"+__import__("sys").argv[1],"rb") as file, httpx.Client(timeout=3600) as client:
    assert client.get("http://nemo:8080/ready").json()["ready"]
    start=time.perf_counter()
    response=client.post("http://nemo:8080/v1/audio/transcriptions", files={"file":(file.name,file,"audio/wav")}, data={"language":"ro","response_format":"verbose_json"})
    wall=time.perf_counter()-start
    response.raise_for_status()
    print(json.dumps({"wall_s":wall,"result":response.json()},ensure_ascii=False))
'''
    rows=[]
    with Monitor(args.output,engine_pid()) as monitor:
        for n in range(args.repeats):
            output=subprocess.check_output(['docker','compose','exec','-T','web','python','-c',script,source.name],cwd=ROOT,text=True)
            row=json.loads(output)
            row['repeat']=n+1
            row['rtfx']=row['result']['duration']/row['wall_s']
            rows.append(row)
            (args.output/'runs.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
            print(json.dumps({k:v for k,v in row.items() if k!='result'}),flush=True)
    summary={'runs':len(rows),'peak_rss_mib':monitor.peak_rss_mib,'peak_gpu_total_mib':monitor.peak_gpu_mib,
             'peak_engine_gpu_mib':monitor.peak_engine_gpu_mib,'peak_gpu_util_percent':monitor.peak_gpu_util}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2))
    source.unlink(missing_ok=True)
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    main()
