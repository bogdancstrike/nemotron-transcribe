#!/usr/bin/env python3
"""Measure a real 1 GiB multipart upload while another job owns the worker."""
import json
import struct
import subprocess
import threading
import time
from pathlib import Path

import httpx
import psutil

ROOT=Path(__file__).resolve().parents[1]


def main():
    with httpx.Client(base_url='http://127.0.0.1:7860',timeout=300) as client:
        jobs=client.get('/api/jobs').raise_for_status().json()['jobs']
        if not any(j['status']=='processing' for j in jobs):
            raise SystemExit('Run this check while another job owns the worker; the test upload is deleted before inference')
        container=subprocess.check_output(['docker','compose','ps','-q','web'],text=True,cwd=ROOT).strip()
        pid=int(subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',container],text=True))
        parent=psutil.Process(pid)
        def rss():
            return sum(p.memory_info().rss for p in [parent,*parent.children(recursive=True)])
        baseline=rss(); samples=[]; stop=threading.Event()
        def monitor():
            while not stop.wait(.05):
                samples.append(rss())
        thread=threading.Thread(target=monitor,daemon=True)
        path=ROOT/'evidence/samples/streaming-1GiB.wav'
        size=1024**3
        with path.open('wb') as file:
            file.write(struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',size+36,b'WAVE',b'fmt ',16,1,2,48000,48000*8,8,32,b'data',size))
            file.truncate(size+44)
        thread.start(); started=time.perf_counter()
        try:
            with path.open('rb') as file:
                created=client.post('/api/jobs',files={'file':('streaming-1GiB.wav',file,'audio/wav')}).raise_for_status().json()
            status=client.get('/api/jobs/'+created['id']).raise_for_status().json()
            client.delete('/api/jobs/'+created['id']).raise_for_status()
            assert status['status']=='queued','Test recording was claimed unexpectedly'
        finally:
            stop.set();thread.join();path.unlink(missing_ok=True)
        result={'bytes':size+44,'wall_s':time.perf_counter()-started,'web_rss_baseline_mib':baseline/1024**2,
                'web_rss_peak_mib':max(samples+[baseline])/1024**2,
                'web_rss_growth_mib':(max(samples+[baseline])-baseline)/1024**2,
                'result':'uploaded, audio-probed and queued; deleted before inference'}
        (ROOT/'evidence/upload-memory.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
