"""Paired all-case scorer, reusing the pre-existing 70ms maximum-cardinality matcher."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "beats"))
from beat_eval import event_score, aggregate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    load = lambda name: json.loads((args.root / name).read_text(encoding="utf-8"))
    frozen = load("FREEZE.json")
    for path, expected in frozen["files"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen file changed: {path}")
    data, reference, production = load("data.json"), load("reference.json"), load("production.json")
    ref_index = {item["key"]: item for item in reference["cases"]}
    prod_index = {item["key"]: item for item in production["cases"]}
    result = {"question": frozen["question"], "limitations": frozen["limitations"], "cases": []}
    for item in data["cases"]:
        ref, prod = ref_index.get(item["key"], {}), prod_index.get(item["key"], {})
        if ref.get("pcmSha256") not in (None, item["pcm_sha256"]) or prod.get("pcmSha256") not in (None, item["pcm_sha256"]):
            raise ValueError("Methods did not use identical PCM bytes")
        old, model, actual = ref.get("old", {}), ref.get("reference", {}), prod.get("production", {})
        record = {"key": item["key"], "pcm_sha256": item["pcm_sha256"],
                  "errors": {"reference": ref.get("error"), "production": prod.get("error")},
                  "old_beats": event_score(item["reference_beats"], old.get("beats", [])),
                  "new_beats": event_score(item["reference_beats"], actual.get("beats", [])),
                  "new_downbeats": event_score(item["reference_downbeats"], actual.get("downbeats", [])),
                  "node_candidate_beats": event_score(item["reference_beats"], model.get("beats", [])),
                  "node_candidate_downbeats": event_score(item["reference_downbeats"], model.get("downbeats", [])),
                  "events_exact_vs_reference": {name: name in actual and actual[name] == model.get(name) for name in ["beats", "downbeats"]},
                  "cost": {"old_ms": old.get("milliseconds"), "node_candidate_ms": model.get("milliseconds"),
                           "production_request_ms": prod.get("wallMs"),
                           "production_diagnostics": actual.get("diagnostics"), "node_process_rss_sample_bytes": model.get("rssBytes")},
                  "excluded_tail_references": item["excluded_tail_references"]}
        record["beat_f1_delta"] = record["new_beats"]["f1"] - record["old_beats"]["f1"]
        result["cases"].append(record)
    for name in ["old_beats", "new_beats", "new_downbeats", "node_candidate_beats", "node_candidate_downbeats"]:
        result[name] = aggregate([item[name] for item in result["cases"]])
    result["paired_delta"] = {name: result["new_beats"][name] - result["old_beats"][name] for name in ["macro_f1", "micro_f1"]}
    result["track_outcomes"] = {"improved": sum(item["beat_f1_delta"] > 0 for item in result["cases"]),
                                "unchanged": sum(item["beat_f1_delta"] == 0 for item in result["cases"]),
                                "worse": sum(item["beat_f1_delta"] < 0 for item in result["cases"])}
    result["production_reference_event_parity"] = all(all(item["events_exact_vs_reference"].values()) for item in result["cases"])
    result["failures"] = {"reference": reference["failures"], "production": production["failures"]}
    result["cost"] = {"reference_model_load_ms": reference["modelLoadMs"], "note": "Single runs, process RSS samples are not exact peak; production includes transport/serialization and first-request startup."}
    for field in ["old_ms", "production_request_ms", "node_candidate_ms"]:
        values = [item["cost"][field] for item in result["cases"] if item["cost"][field] is not None]
        result["cost"][field] = {"count": len(values), "total": sum(values), "median": statistics.median(values) if values else None, "min": min(values) if values else None, "max": max(values) if values else None}
    (args.root / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()
