---
title: "which image classification model should i use on raspberry pi 5"
type: queries
tags: ["deliberation", "sage"]
created: 2026-07-05
updated: 2026-07-05
status: draft
---

## Query
which image classification model should i use on raspberry pi 5

## Sage Synthesis
For image classification on Raspberry Pi 5, MobileNetV2 remains the strongest choice balancing accuracy and speed. At ~1MB model size with ~4GB RAM, it runs at ~30-60 FPS via TensorLite or TVM compilation, delivering 71.8% top-1 accuracy on ImageNet—a sweet spot for edge deployment.

If your latency budget allows more computation (e.g., batch inference or real-time video), EfficientNet-Lite0 offers better accuracy (~76%) at the cost of ~2MB RAM and slower inference (~15 FPS). For tasks requiring sub-30ms per frame, consider ShuffleNet-V2 or SqueezeNet, which sacrifice some accuracy for CPU-only inference without GPU acceleration.

If you need to run multiple models simultaneously (e.g., classification + object detection), prefer quantized INT8 variants via NNAPI or TVM. The Pi 5's PCIe bus enables direct GPU access on compatible boards—compile with `--use-gpu=true` in the model runtime for meaningful speedups, though results vary by board variant.

## Analogies
