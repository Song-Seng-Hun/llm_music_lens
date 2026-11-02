# Beat This small0 server model

`small0-250.bin` is ONNX, not executable code. Derived from the official
[Beat This](https://github.com/CPJKU/beat_this) small0 checkpoint. Upstream
explicitly releases code and published model weights under MIT; see LICENSE.

- Source checkpoint: `https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/small0.ckpt`
- Checkpoint SHA256: `6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b`
- Derived ONNX SHA256: `2c61c460654723857fc8dface211d133e6c983efec4c5e91b576bfca746182f1`
- Bytes: 9,426,750; opset17; float32 features `[1,250,128]`.
- Conversion: `research/timing/sites/export-beat.py` (repo root), Torch2.8.0+cpu,
  ONNX1.16.1, BeatThis1.1.0. Architecture/weights unchanged; fixed5s context.
- `.bin` allows Cloudflare to package the bytes as a Data module; the runtime
  cannot depend on Python or files in the research evidence directory.

Small0 excludes GTZAN from training per the upstream README. Our inspected
12-track subset is still not a fresh generalization study. Short-window
inference must be evaluated separately from upstream full-context accuracy.
No test audio or annotations are included here.
