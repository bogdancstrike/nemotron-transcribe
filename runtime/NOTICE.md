# Runtime patch and container attribution

`Dockerfile` is adapted from NVIDIA/NeMo-Speech.cpp commit
`20999cc921d2b14aa58708f61756a8b2e787ba20`, `docker/Dockerfile`.
Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. Apache-2.0.

Modifications: fetch an exact upstream revision during the source stage, verify
and apply the local SALM expert-offload patch, and copy that prepared source tree
into the original NVIDIA build stages. NVIDIA's runtime license/NOTICE and
third-party license installation steps remain intact.

`hybrid.patch` contains changes to Apache-2.0 NVIDIA source. It adds a SALM
configuration field and routes selected expert tensors through the existing
llama.cpp CPU expert-placement helper. Mixed execution uses the standard ggml
scheduler, not GPU-only fused serving. The source license headers are retained.
The Apache-2.0 license and distribution conditions were inspected for this
source-only patch; this is a technical license review, not legal certification.

The exact model weights are downloaded separately. The model uses OpenMDW 1.1;
weights are never included in Git, screenshots, or Docker build contexts.
Community GGUF conversion: AtomicChat. Runtime: NVIDIA. ggml/llama.cpp:
ggml-org contributors, MIT; third-party notices are retained by the image build.
