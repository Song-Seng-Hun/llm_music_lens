"""Freeze complete-recipe comparison before predictions; retain failed numerical gate."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if any((root / name).exists() for name in ["FREEZE.json", "reference.json", "production.json"]):
        raise ValueError("Do not overwrite freeze or refreeze after predictions")
    load = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
    data, parity, features = load("data.json"), load("parity.json"), load("features.json")
    keys = [case["key"] for case in data["cases"]]
    expected = [f"gtzan_{genre}_{n:05d}" for genre in ["blues", "classical", "hiphop", "rock"] for n in range(3)]
    if keys != expected or len(parity["cases"]) != 12 or any("error" in case for case in parity["cases"]):
        raise ValueError("Exact12 dataset and completed all-case frontend diagnostic required")
    digest = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest("web/lib/server/beat-frontend.mjs") != parity["frontendSha256"]:
        raise ValueError("Frontend changed since parity")
    if digest("web/lib/server/beat-frontend-constants.mjs") != parity["constantsSha256"]:
        raise ValueError("Frontend constants changed since parity")
    paths = ["web/public/precision-rhythm.mjs", "web/public/analysis/tempo-grid.mjs", "web/public/analysis/acoustics.mjs",
             "web/lib/server/beat-analysis.mjs", "web/lib/server/beat-frontend.mjs", "web/lib/server/beat-frontend-constants.mjs",
             "web/lib/server/beat-postprocess.mjs", "web/lib/server/beat-runtime.mjs", "research/timing/beats/beat_eval.py"]
    paths += [str(path) for path in Path("research/timing/comparison").glob("*.py")]
    paths += ["research/timing/comparison/run.mjs", str(root / "data.json"), str(root / "features.json"), str(root / "parity.json")]
    paths += [str(root / case["pcm_path"]) for case in data["cases"]]
    paths += [str(root / case["path"]) for case in features["cases"]]
    record = {
        "phase": "Confirm: narrow practical paired comparison on a previously inspected fixed subset",
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "question": "Does the deployable fixed5s small0 complete recipe improve paired beat F1 over current analyzeRhythm on these same12 raw waveforms, and at what measured cost?",
        "primary": "Equal-track macro beat F1 delta; inclusive70ms maximum-cardinality one-to-one matching. All12 fixed cases and failed cases (empty predictions) retained.",
        "secondary": ["Pooled beat F1 and per-track wins/losses", "New downbeat absolute F1 against position1 truth, never strongBeats baseline", "Actual local MCP end-to-end cost; shared-JS NodeORT cost separately", "Exact event equality between shared-JS NodeORT reference and actual production MCP"],
        "minimum_effect": "Any positive macro beat F1 delta is an observed gain on this fixed set; report size, losses and cost, with no invented generalization/significance threshold.",
        "candidate": {"checkpoint": "small0", "context_frames": 250, "fps": 50, "border": 6, "stride": 238, "overlap": "keep_first", "avoid_short_end": True, "decoder": "official minimal", "threshold": 0, "peak_width": 7},
        "budget": {"old_runs_per_case": 1, "shared_JS_NodeORT_reference_runs_per_case": 1, "actual_MCP_runs_per_case": 1, "cases": 12, "tuning_runs": 0, "http_timeout_seconds_per_case": 120, "parallel_inference": False},
        "cost": "Model loaded once; fixed case order; old full analyzeRhythm runtime includes other spectral outputs; production includes HTTP/base64/provenance and first-request startup. RSS samples are not exact peak. No repeated latency benchmark.",
        "frontend_gate": {"status": "FAILED", "mismatched_values": sum(case["mismatches"] for case in parity["cases"]), "total_values": sum(case["values"] for case in parity["cases"]), "original_tolerance": parity["tolerance"], "decision_before_predictions": "Parent explicitly selected direct product-recipe evaluation after one numerical repair. Keep the failed gate failed; do not claim official frontend numerical equivalence. No further frontend tuning."},
        "limitations": ["small0 excludes GTZAN from training per official README, but these12 were previously inspected and are a convenience subset.", "Original numerical frontend equivalence gate failed; actual complete JS recipe is scored directly, not replaced with official features.", "Only four genres/30s excerpts; no full-song or unbiased population improvement claim.", "Prior1500-frame spectrogram-only results are contextual only, not the paired baseline.", "Annotation ambiguity and corpus duplicates remain possible; no post-hoc label edits."],
        "modelPath": str(args.model.resolve()), "modelSha256": digest(args.model),
        "files": {str(Path(path).resolve()): digest(path) for path in paths},
    }
    (root / "FREEZE.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"freeze_sha256": digest(root / "FREEZE.json"), "cases": len(keys), "tuning": 0}))


if __name__ == "__main__":
    main()
