# Browser analysis components

- Spotify Basic Pitch TypeScript 1.0.1, including its bundled model: Apache-2.0; see basic-pitch.txt. https://github.com/spotify/basic-pitch-ts
- TensorFlow.js 3.21.0 / WASM backend, Copyright 2022 Google LLC: Apache-2.0; see tensorflow-js.txt. License copied from the upstream tfjs-v3.21.0 tag. https://github.com/tensorflow/tfjs
- Transformers.js 3.8.1: Apache-2.0; see transformers-js.txt. https://github.com/huggingface/transformers.js
- ONNX Runtime Web 1.22.0-dev.20250409-89f8206ba4: MIT; see onnxruntime.txt and onnxruntime-third-party.txt, copied from upstream commit 89f8206ba4. https://github.com/microsoft/onnxruntime
- esbuild retains bundled JavaScript legal comments in /precision-worker.mjs.LEGAL.txt.

Whisper weights are not bundled or proxied. The browser optionally downloads Xenova/whisper-base or Xenova/whisper-small (model-card license Apache-2.0) at the revisions in analysis/models.mjs. Model cards: https://huggingface.co/Xenova/whisper-base and https://huggingface.co/Xenova/whisper-small. Public downloads do not require an inference API key.

Music Lens changes: bounded note windows and merging, model/runtime loading, an explicit optional-dtype default for the TensorFlow.js WASM Fill kernel. Upstream weight bytes remain unchanged. /models/manifest.json records the bundled model/runtime sizes and SHA-256 hashes.
