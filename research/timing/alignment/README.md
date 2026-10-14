# Local known-phone singing alignment

This research runner preserves the TIFA backend used in the timing pilot. It accepts known model-vocabulary phone symbols and audio, then emits learned phone spans. It does not transcribe lyrics, run G2P, divide word durations, or infer syllable grouping. It is not connected to the application or an API.

## Local assets and environment

Obtain these official assets separately, and keep them under the Git-ignored `evidence/` directory. The runner never downloads assets. It verifies the Python source snapshot and the three required model files before importing TIFA or loading weights.

- [TIFA source](https://github.com/openvpi/TIFA/tree/32a0a13aebce580844ad1dc281ea11ef1be94f14), exact commit `32a0a13aebce580844ad1dc281ea11ef1be94f14`. The source fingerprint includes all `.py` relative paths and contents, with CRLF normalized to LF. Use a clean source extraction without a virtual environment inside it.
- [Official TIFA-1.0-ST release](https://github.com/openvpi/TIFA/releases/tag/v1.0.0), archive `TIFA-1.0-ST.zip`, 150,789,526 bytes, SHA256 `6da6832cd2cb981aae1ccc2d9330f8f7c085ce7977d1eb13bff63531d038a88b`. `--model-root` points to the extracted directory containing `model.pt`, `config.yaml` and `vocabulary.json`.

The tested environment is Python 3.10, Torch `2.8.0+cpu`, and the exact direct dependencies in `requirements.txt`. Torchaudio `2.8.0+cpu` was installed in the shared pilot environment but is not imported by this runner. To create another isolated environment from the repository root:

```powershell
py -3.10 -m venv evidence/timing-local-env
& evidence/timing-local-env/Scripts/python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
& evidence/timing-local-env/Scripts/python.exe -m pip install -r research/timing/alignment/requirements.txt
```

These are explicit environment setup commands, not actions performed by the runner. Do not install globally or commit environments, model weights, audio, reference labels or prediction text.

## Input and execution

Create a local JSON object with exactly these fields; this short example describes the shape, not an evaluation fixture:

```json
{"audio": "clip.wav", "phones": ["ja/k", "ja/a"]}
```

Relative audio paths resolve against the input JSON directory. Provide one mono 48kHz audio file, at most 60 seconds, and 1–512 phone symbols present in the model's `vocabulary.json`. Phone strings must already use its spelling and language prefixes. `AP` is available for known aspirations. Silence is handled by decoder gaps, not a fabricated `SP` token. The runner does not accept gold times or perform phone mapping.

```powershell
& evidence/timing-local-env/Scripts/python.exe research/timing/alignment/align.py `
  --source-root evidence/timing-assets/TIFA-32a0a13aebce580844ad1dc281ea11ef1be94f14 `
  --model-root evidence/timing-assets/TIFA-1.0-ST `
  --input evidence/timing-input/input.json `
  --output evidence/timing-input/aligned.json
```

The output directory must exist and the output file must not. The runner uses CPU float32, four Torch/Numba threads, skip penalty 0.5 and no pronunciation groups, matching the pilot. `torch.load(weights_only=True)`, `yaml.safe_load`, tensor checks and strict state-dict loading are required; there is no unsafe loader fallback. Numba's compilation cache is local to the output directory.

`phones`, `tokens` and `spans_seconds` have matching indices. Zero-width skipped spans remain present. `agreement_uncalibrated` is the model's token-agreement diagnostic, **not calibrated timing confidence**. Hashes identify input/audio/model, and timings include first-use compilation when applicable. Failures exit nonzero; existing output is never overwritten.

## Evidence limits and licenses

The original run used two Japanese GTSinger clips from one singer/song with oracle phone identity/order. Both completed, but TIFA's training list includes GTSinger, and large boundary errors remained despite high agreement. This established local execution only; general alignment quality, automatic lyrics conversion, syllable segmentation, Korean and accompaniment handling remain unverified. The original pilot's inputs, scorer, predictions and error logs remain under ignored `evidence/timing-extensions-09/alignment/`; the root research notes report its results.

This parameterized wrapper was checked with `--help`, safe invalid-input/missing-asset cases, and asset/input validation against one frozen pilot clip. Source/model hashes and translated token IDs matched. A subsequent single packaging check ran the real CLI on that same clip0000: it exited0, and independent review found duration, token IDs, every span and uncalibrated agreement exactly equal to the original output. This verifies the callable entry point; it adds no quality sample and changes none of the pilot results. Evidence: `runner-smoke/packaged-prediction.json` and `packaged-run.log` under the original local alignment evidence directory.

[TIFA code license](https://github.com/openvpi/TIFA/blob/32a0a13aebce580844ad1dc281ea11ef1be94f14/LICENSE) is MIT. The official model release specifies CC BY-NC-SA4.0 for the model file. GTSinger is separately CC BY-NC-SA4.0 with additional terms in its dataset license; no GTSinger material is included here. Review applicable asset terms for any use beyond this local research.
