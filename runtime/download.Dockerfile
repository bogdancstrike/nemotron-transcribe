FROM python:3.12-slim-bookworm
WORKDIR /app
RUN pip install --no-cache-dir huggingface_hub==0.36.0
COPY scripts/download_model.py /app/scripts/download_model.py
COPY runtime.lock.json /app/runtime.lock.json
ENV MODELS_DIR=/models HF_HOME=/models/.hf-cache HF_HUB_DISABLE_TELEMETRY=1
CMD ["python", "/app/scripts/download_model.py"]
