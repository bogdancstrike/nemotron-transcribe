# Model provenance and hardware requirements

Verified references as of **2026-10-31**:

- Official model (upstream): https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B
- Community prequantized GGUF: https://huggingface.co/AtomicChat/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF
- Official GGUF runtime: https://github.com/NVIDIA/NeMo-Speech.cpp
- SALM guide: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/salm/README.md
- SALM resource configuration: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/salm/configuration.md
- HTTP API: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/api.md

Architecture: approximately 34 billion parameters total / 4.3 billion active in NVIDIA's official model card; model supports up to one-hour audio input and 40 languages including Romanian. These are **not** 4.3B total resident parameters; CPU/GPU storage tracks quantized model weight size.

## Exact required HF pair

```text
repo: AtomicChat/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF
NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-AD-IQ4_NL.gguf   19.68 GB decimal
NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-Encoder-F16.gguf  1.67 GB decimal
TOTAL ON DISK ~21.35 GB (+ runtime caches and audio buffers)
```

Alternative decoder files from same repo: `AD-Q5_0_XL` ~29.26 GB, `Q8_0` ~35.00 GB, `AD-Q2_0_L` ~15.55 GB, `AD-Q2_0_M` ~13.57 GB. Encoder remains mandatory. Quantization quality on Romanian has not been validated separately by the community author. Use IQ4_NL as starting quality/memory compromise.

### RAM / VRAM

| Mode | GPU | RAM | Status |
|---|---|---|---|
| Stock runtime, IQ4_NL CPU | No GPU required | >=~21.35 GB weights + overhead; 64 GB host recommended here | Documented CPU backend; real speed must be measured |
| Stock runtime, IQ4_NL fully GPU | ~24 GB or more practical minimum | Minimal | Not compatible with 10 GB GPU |
| Patched hybrid MoE experts on CPU | RTX 3080 10 GB target | 64 GB target | **Experimental**. Requires patch and measured proof |
| Original BF16 | A100 80 GB class | Smaller than local CPU setup | Official production configuration |

**Offload is not equivalent to moving the whole file to RAM while CUDA runs.** A working patch must explicitly allocate some model layers/experts in host RAM and evaluate them on CPU while other tensors and attention/encoder use GPU. PCIe transfer and RAM bandwidth may dominate.

## License and trust checks

- Runtime code Apache-2.0 + third-party notices; model weights OpenMDW License 1.1. Read the original license before distributing a product.
- GGUF is community-converted, not an NVIDIA-maintained official checkpoint. Verify model filename, reported tensor architecture, hash and upstream revisions. Pin HF snapshot SHA in real deployments.
- No model assets or NVIDIA library source are redistributed inside this handoff ZIP. They are fetched separately by opt-in scripts.
