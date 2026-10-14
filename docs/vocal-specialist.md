# Solo-vocal note analysis

Select **독창 · 보컬만 (SOME)** under 음표 대상 for an unaccompanied solo voice. The default mixed/polyphonic Basic Pitch route remains available. This is not source separation: a full arrangement, harmonized vocals or a rap passage may need a different analysis route. Whisper words and rhythm measurements remain separate estimates.

The selected model runs locally in the browser (ONNX/WASM), with no API key or audio upload. It returns continuous MIDI pitch, note durations and rests. JSON preserves fractional MIDI and `confidence: null` because this model does not emit confidence. Note names round only for display, retaining the signed cent offset. A duration-weighted melody key is tentative; accompaniment chords are withheld in this mode.

## Prepare

From the project root:

```text
python scripts/prepare-vocal-model.py
node scripts/prepare-analysis.mjs
```

An existing `some-0.0.1.oudep` can be passed as the Python script's first argument. The script verifies archive and model SHA256 and extracts only the literal ONNX member; no downloaded installer or Python is executed. The optional model is about 450 MiB and is not committed. Missing model => visible failed note stage, no hidden replacement by another model. Cancellation terminates the worker.

Deployment remains separate and unverified: the 471,381,796-byte weight asset may exceed a host's per-file limit. This change installs and verifies the local program, not a published Sites deployment. We do not claim that copying this asset directly will deploy on Sites.

## Provenance

- Model: [openvpi/SOME](https://github.com/openvpi/SOME), distributed in [OpenUtau some-0.0.1.oudep](https://github.com/xunmengshe/OpenUtau/releases/download/0.0.0.0/some-0.0.1.oudep).
- Archive SHA256: `b98a350b60740e3a153b15e137ed025832956d918c44853048bec93af657f478`.
- ONNX member `0918_continuous128_clean_3spk.onnx`, SHA256: `9d55f9d239e9d70d1bf039506cf1c428b657df7bbb8b94104ca882af6b721e06`.
- Configuration: mono float32, 44,100 Hz, graph contains feature extraction/decoding. Output `note_midi`, `note_rest`, `note_dur` seconds. Native rest durations advance the clock.
- Code MIT; released model weights [CC BY-NC-SA 4.0](https://github.com/openvpi/SOME/releases/tag/v0.0.1). Local evaluation here is noncommercial. Retain attribution/license when preparing a later distribution.

## Measured accuracy and limits

Evidence: `evidence/vocal-specialist-02/`. Two development and two locally heldout [vocadito v3](https://zenodo.org/records/5578807) human singing clips; original audio, musician A1 labels primary, A2 sensitivity. Model training overlap is unknown. Onset tolerance 50 ms, pitch tolerance 50 cents. All labels, metrics and failure records are retained.

The initial integer-rounded pilot failed the frame gate. Native fractional output corrects that lossy adaptation; this was recorded before confirmation. The confirmation question, source hashes, model, decoding and criteria were frozen in `CONFIRM.json` before either heldout inference.

Heldout mean A1 event F1: **48.56% Basic Pitch -> 71.04% SOME**. Both clips improved event recall and note-frame F1; A2 also improved. This supports a local solo-vocal option, not general song accuracy. Note offsets are not directly part of event F1; note-frame F1 adds duration/pitch evidence. False/missed notes remain. No lyric, chord, beat, rap or Vocaloid accuracy claim follows from this set.

Whole evaluation clips fit one inference window. Longer inputs use 40-second cores plus 2-second context; boundary accuracy on long recordings is unvalidated. Maximum input remains 10 minutes/12 MiB; 4,000 note events. Notes outside MIDI 21–108 are explicitly omitted with partial status. Runtime depends on the computer; the 33-second development clip took about 42 seconds of model inference here, plus loading and other analysis.
