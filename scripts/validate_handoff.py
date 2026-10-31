#!/usr/bin/env python3
"""Quick local static checks; not a replacement for docker build/model inference."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
for relative in (
    'README.md', 'AGENTS.md', 'PROMPT_FOR_AGENT.md', 'tasks.yaml',
    'compose.yaml', 'compose.hybrid.example.yaml', '.env.example',
    'docs/01-product-spec.md', 'docs/02-architecture.md',
    'docs/03-implementation-plan.md', 'docs/04-acceptance-criteria.md',
    'docs/05-model-runtime.md', 'docs/06-api-security.md',
    'docs/07-benchmark-plan.md', 'docs/08-sources.md',
    'app/main.py', 'app/worker.py', 'app/static/index.html',
):
    assert (ROOT / relative).exists(), f'Missing {relative}'
for filename in ('compose.yaml', 'compose.hybrid.example.yaml', 'tasks.yaml'):
    assert isinstance(yaml.safe_load((ROOT / filename).read_text()), dict)
assert '--asr.salm.n_cpu_moe' not in (ROOT / 'compose.yaml').read_text(), 'CPU baseline must not invent hybrid support'
assert 'n_cpu_moe' in (ROOT / 'compose.hybrid.example.yaml').read_text()
assert 'RTX 3080' in (ROOT / 'docs/04-acceptance-criteria.md').read_text()
print('Handoff structure and example YAML: OK. This does not validate the Docker/model runtime.')
