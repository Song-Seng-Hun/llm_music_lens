"""Sung notes of the Demucs vocal stem from YourMT3+ (Apache-2.0, mimbres/YourMT3), run by rhythm_worker.py in its own
environment.

Usage: <yourmt3 python> -I sing_notes.py MODEL_DIR IN.wav OUT.json
MODEL_DIR holds code/ (model_helper.py and amt/src of the Space at the commit in SOURCE.txt) and the weights-only
checkpoint under amt/logs/2024/<EXP>/checkpoints/last.ckpt; the worker checks its SHA-256 before it starts this. OUT.json
holds [[onset, offset, midi], ...] of the notes on program 100 (singing voice), drums and other programs left out. The
steps follow model_helper.transcribe; on RWC-P songs 76-100 the same steps gave pitch F 54.2 against 30.5 for RMVPE
notes (see "Sung notes" in README.md). Every checkpoint read is forced to weights_only. The CPU soft limit is raised to
the hard limit the worker set for this step.
"""
import json
import os
import resource
import sys

EXP = "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops"
ARGS = [EXP + "@last.ckpt", "-p", "2024", "-tk", "mc13_full_plus_256", "-dec", "multi-t5", "-nl", "26", "-enc", "perceiver-tf",
        "-sqr", "1", "-ff", "moe", "-wf", "4", "-nmoe", "8", "-kmoe", "2", "-act", "silu", "-epe", "rope", "-rp", "1",
        "-ac", "spec", "-hop", "300", "-atc", "1", "-pr", "32"]
SINGING = 100  # YourMT3 program for singing voice (101 is chorus)


def main(model_dir, wav, out):
    hard = resource.getrlimit(resource.RLIMIT_CPU)[1]
    resource.setrlimit(resource.RLIMIT_CPU, (hard, hard))  # the worker's soft 600 s; a 600 s vocal needs about 1000 s CPU
    code = os.path.join(model_dir, "code")
    sys.path[:0] = [os.path.join(code, "amt", "src"), code]
    os.chdir(model_dir)  # the checkpoint path is relative: amt/logs/2024/...
    import torch
    import torchaudio
    load = torch.load

    def safe_load(*a, **k):  # model_helper passes weights_only=False
        k["weights_only"] = True
        return load(*a, **k)

    torch.load = safe_load
    from model_helper import load_model_checkpoint
    from utils.audio import slice_padded_array
    from utils.event2note import merge_zipped_note_events_and_ties_to_notes
    from utils.note2event import mix_notes

    model = load_model_checkpoint(args=ARGS, device="cpu")
    cfg = model.audio_cfg
    audio, sr = torchaudio.load(uri=wav)
    audio = torchaudio.functional.resample(torch.mean(audio, dim=0).unsqueeze(0), sr, cfg["sample_rate"])
    segs = torch.from_numpy(slice_padded_array(audio, cfg["input_frames"], cfg["input_frames"]).astype("float32")).unsqueeze(1)
    with torch.no_grad():
        tokens, _ = model.inference_file(bsz=8, audio_segments=segs)
    starts = [cfg["input_frames"] * k / cfg["sample_rate"] for k in range(segs.shape[0])]
    per_ch = []
    for ch in range(model.task_manager.num_decoding_channels):
        zipped, _, _ = model.task_manager.detokenize_list_batches([a[:, ch, :] for a in tokens], starts, return_events=True)
        per_ch.append(merge_zipped_note_events_and_ties_to_notes(zipped)[0])
    rows = [[float(n.onset), float(n.offset), int(n.pitch)] for n in mix_notes(per_ch) if n.program == SINGING and not n.is_drum]
    with open(out, "w") as f:
        json.dump(rows, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
