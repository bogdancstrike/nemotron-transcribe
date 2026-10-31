#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ ! -f .env ]; then cp .env.example .env; fi
mkdir -p models data vendor evidence
if ! grep -q '^HOST_UID=' .env; then echo "HOST_UID=$(id -u)" >> .env; fi
if ! grep -q '^HOST_GID=' .env; then echo "HOST_GID=$(id -g)" >> .env; fi
runtime_sha="$(python3 -c 'import json; print(json.load(open("runtime.lock.json"))["upstream_commit"])')"
if [ ! -d vendor/NeMo-Speech.cpp/.git ]; then
  git clone --no-checkout https://github.com/NVIDIA/NeMo-Speech.cpp.git vendor/NeMo-Speech.cpp
fi
if [ -n "$(git -C vendor/NeMo-Speech.cpp status --porcelain --untracked-files=no)" ]; then
  echo 'Runtime checkout has local changes; refusing to overwrite them.' >&2
  exit 1
fi
if ! git -C vendor/NeMo-Speech.cpp cat-file -e "$runtime_sha^{commit}" 2>/dev/null; then
  git -C vendor/NeMo-Speech.cpp fetch --depth 1 origin "$runtime_sha"
fi
git -C vendor/NeMo-Speech.cpp checkout --detach "$runtime_sha"
# Upstream Dockerfile installs these licenses even with Flashlight disabled.
git -C vendor/NeMo-Speech.cpp submodule update --init --depth 1 \
  llama.cpp third_party/cpp-httplib third_party/flashlight-text third_party/kenlm
git -C vendor/NeMo-Speech.cpp rev-parse HEAD > evidence/upstream-commit.txt
git -C vendor/NeMo-Speech.cpp submodule status > evidence/upstream-submodules.txt
docker compose config > evidence/docker-compose-config.txt
echo 'Pinned checkout ready. Weights are not downloaded by bootstrap.'
echo 'Explicit next step: docker compose --profile setup run --rm model-download'
