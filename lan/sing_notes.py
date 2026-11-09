"""Sung notes of the Demucs vocal stem from YourMT3+ (Apache-2.0, mimbres/YourMT3), run by rhythm_worker.py in its own
environment.

Usage: <yourmt3 python> -I sing_notes.py MODEL_DIR IN.wav OUT.json
MODEL_DIR holds code/ (model_helper.py and amt/src of the Space at the commit in SOURCE.txt) and the weights-only
checkpoint under amt/logs/2024/<EXP>/checkpoints/last.ckpt; the worker checks its SHA-256 before it starts this. OUT.json
holds [[onset, offset, midi], ...] of the notes on program 100 (singing voice), drums and other programs left out. The
steps follow model_helper.transcribe; on RWC-P songs 76-100 the same steps gave pitch F 54.2 against 30.5 for RMVPE
notes (see "Sung notes" in README.md). Only the decoder channel that holds program 100 is decoded, two segments at a
time: the multi-channel decoder folds channels into the batch with shared weights, so that channel's tokens are the same
as in the full 13-channel decode, at about half the time and a third of the memory. A model without that structure gets
the full decode (batch 8), with a line on stderr. Every checkpoint read is forced to weights_only. The CPU soft limit is
raised to the hard limit the worker set for this step.
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
BSZ, FULL_BSZ = 2, 8  # segments per batch: singing channel only, full decode


class OtherModel(Exception):
    """The encoder output does not have the channel layout decode_channel slices."""


def singing_channel(model):
    """The decoder channel that holds SINGING, or None when the model is not the multi-channel build this expects."""
    tm = model.task_manager
    try:
        ch = tm.program2channel_vocab[SINGING]["channel"]
        n = tm.num_decoding_channels
        ok = model.decoder_type == "multi-t5" and model.decoder.num_channels == n and 0 <= ch < n and all(
            callable(getattr(model, a)) for a in ("spectrogram", "pre_encoder", "encoder", "pre_decoder", "embed_tokens",
                                                  "lm_head", "shift_right_fn"))
    except (AttributeError, KeyError, TypeError):
        return None
    return ch if ok else None


def decode_channel(model, ch, generate):
    """An inference(x) for model.inference_file that decodes only channel ch: the encoder output is sliced to it and the
    decoder runs one channel. generate is YourMT3's task_cond_dec_generate. Tokens come back as (B, 1, L)."""
    def inference(x, task_tokens=None, **kwargs):
        enc = model.pre_decoder(model.encoder(inputs_embeds=model.pre_encoder(model.spectrogram(x)))["last_hidden_state"])
        n = model.decoder.num_channels
        if enc.dim() != 4 or enc.shape[1] != n:
            raise OtherModel("encoder output %s, not %d channels" % (tuple(enc.shape), n))
        model.decoder.num_channels = 1
        try:
            return generate(decoder=model.decoder, decoder_type=model.decoder_type, embed_tokens=model.embed_tokens,
                            lm_head=model.lm_head, encoder_hidden_states=enc[:, ch:ch + 1], shift_right_fn=model.shift_right_fn,
                            prefix_ids=None, max_length=model.max_total_token_length)
        finally:
            model.decoder.num_channels = n
    return inference


def main(model_dir, wav, out):
    hard = resource.getrlimit(resource.RLIMIT_CPU)[1]
    resource.setrlimit(resource.RLIMIT_CPU, (hard, hard))  # the worker's soft 600 s; a 600 s vocal needs about 550 s CPU
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
    try:
        from model.t5mod_helper import task_cond_dec_generate
    except ImportError:
        task_cond_dec_generate = None

    model = load_model_checkpoint(args=ARGS, device="cpu")
    cfg = model.audio_cfg
    audio, sr = torchaudio.load(uri=wav)
    audio = torchaudio.functional.resample(torch.mean(audio, dim=0).unsqueeze(0), sr, cfg["sample_rate"])
    segs = torch.from_numpy(slice_padded_array(audio, cfg["input_frames"], cfg["input_frames"]).astype("float32")).unsqueeze(1)
    ch, tokens = singing_channel(model) if task_cond_dec_generate else None, None
    if ch is not None:
        model.inference = decode_channel(model, ch, task_cond_dec_generate)  # shadows the method on this instance
        try:
            with torch.no_grad():
                tokens, _ = model.inference_file(bsz=BSZ, audio_segments=segs)
            cols = 1
        except OtherModel as e:
            sys.stderr.write("sing_notes: %s\n" % e)
        del model.inference
    if tokens is None:
        sys.stderr.write("sing_notes: model structure differs; full decode\n")
        with torch.no_grad():
            tokens, _ = model.inference_file(bsz=FULL_BSZ, audio_segments=segs)
        cols = model.task_manager.num_decoding_channels
    starts = [cfg["input_frames"] * k / cfg["sample_rate"] for k in range(segs.shape[0])]
    per_ch = []
    for i in range(cols):  # one column of tokens per decoded channel
        zipped, _, _ = model.task_manager.detokenize_list_batches([a[:, i, :] for a in tokens], starts, return_events=True)
        per_ch.append(merge_zipped_note_events_and_ties_to_notes(zipped)[0])
    rows = [[float(n.onset), float(n.offset), int(n.pitch)] for n in mix_notes(per_ch) if n.program == SINGING and not n.is_drum]
    with open(out, "w") as f:
        json.dump(rows, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
