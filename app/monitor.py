"""Read-only runtime telemetry. Share NeMo's PID namespace; never mount Docker socket."""
import csv
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.getenv('DATA_DIR', '/data'))


def meminfo():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        values[key] = int(value.split()[0]) * 1024
    return {'total_gib':values['MemTotal'] / 1024**3,
            'available_gib':values['MemAvailable'] / 1024**3,
            'used_gib':(values['MemTotal'] - values['MemAvailable']) / 1024**3,
            'swap_used_gib':(values['SwapTotal'] - values['SwapFree']) / 1024**3}


def cpu_ticks():
    # guest and guest_nice are already included in user/nice.
    values = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def model_rss():
    for line in Path('/proc/1/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1]) / 1024
    return None


def gpu_info():
    try:
        text = subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw','--format=csv,noheader,nounits'], text=True, timeout=5)
        row = next(csv.reader(text.splitlines()))
        gpu = {'name':row[0].strip(),'total_mib':float(row[1]),'used_mib':float(row[2]),
               'utilization_percent':float(row[3]),'temperature_c':float(row[4]),'power_w':float(row[5]),
               'engine_used_mib':0.0}
        processes = subprocess.check_output(['nvidia-smi','--query-compute-apps=process_name,used_gpu_memory','--format=csv,noheader,nounits'], text=True, timeout=5)
        for process in csv.reader(processes.splitlines()):
            if len(process) == 2 and 'nemo-speech' in process[0]:
                gpu['engine_used_mib'] += float(process[1])
        return gpu
    except (OSError, ValueError, subprocess.SubprocessError, StopIteration):
        return None


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    brand = next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), 'Unknown CPU')
    previous = cpu_ticks()
    while True:
        current = cpu_ticks()
        delta = current[0] - previous[0]
        usage = 100 * (1 - (current[1] - previous[1]) / delta) if delta else None
        previous = current
        result = {'sampled_at':datetime.now(timezone.utc).isoformat(), 'cpu':{'name':brand,'logical_cores':os.cpu_count(),'utilization_percent':usage},
                  'ram':meminfo(),'engine_rss_mib':model_rss(),'gpu':gpu_info(),
                  'disk_free_gib':shutil.disk_usage(DATA).free / 1024**3,
                  'backend':os.getenv('NEMO_MODE','cpu'),
                  'cpu_moe_blocks':int(os.getenv('NEMO_CPU_MOE_LAYERS','0')),
                  'patch_commit':os.getenv('NEMO_PATCH_COMMIT') or None}
        temporary = DATA / 'system.json.tmp'
        temporary.write_text(json.dumps(result))
        temporary.replace(DATA / 'system.json')
        time.sleep(2)


if __name__ == '__main__':
    main()
