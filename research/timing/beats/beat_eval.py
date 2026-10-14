"""Fixed 70ms, one-to-one event matching; no model/dataset dependencies."""
import math

TOLERANCE_SECONDS = 0.07


def event_score(reference, prediction):
    def ordered(values):
        result = []
        for index, value in enumerate(values):
            if isinstance(value, bool):
                raise ValueError("Event times must be finite, nonnegative seconds")
            time = float(value)
            if not math.isfinite(time) or time < 0:
                raise ValueError("Event times must be finite, nonnegative seconds")
            result.append((time, index))
        return sorted(result)

    truth, estimate = ordered(reference), ordered(prediction)
    r = p = 0
    matches = []
    # Earliest compatible sorted events maximize cardinality for equal-width
    # intervals. Nearest-first matching can consume another event's only match.
    while r < len(truth) and p < len(estimate):
        actual, reference_index = truth[r]
        predicted, prediction_index = estimate[p]
        if predicted < actual - TOLERANCE_SECONDS:
            p += 1
        elif predicted > actual + TOLERANCE_SECONDS:
            r += 1
        else:
            matches.append({"reference_index": reference_index, "prediction_index": prediction_index,
                            "absolute_error_seconds": abs(predicted - actual)})
            r += 1
            p += 1
    tp, nr, np = len(matches), len(truth), len(estimate)
    return {"tolerance_seconds": TOLERANCE_SECONDS, "reference_count": nr, "prediction_count": np,
            "true_positive": tp, "false_positive": np - tp, "false_negative": nr - tp,
            "precision": tp / np if np else (0.0 if nr else None),
            "recall": tp / nr if nr else None,
            "f1": 2 * tp / (nr + np) if nr + np else None, "matches": matches}


def aggregate(scores):
    available = [score for score in scores if score is not None]
    tp = sum(score["true_positive"] for score in available)
    fp = sum(score["false_positive"] for score in available)
    fn = sum(score["false_negative"] for score in available)
    defined_f1 = [score["f1"] for score in available if score["f1"] is not None]
    return {"scored_tracks": len(available), "true_positive": tp, "false_positive": fp, "false_negative": fn,
            "micro_f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "macro_f1": sum(defined_f1) / len(defined_f1) if defined_f1 else None,
            "undefined_f1_tracks": len(available) - len(defined_f1)}
