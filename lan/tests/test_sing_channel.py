"""sing_notes.py decodes only the singing channel (two segments a batch), and falls back to the full decode (batch 8) when the
model is built differently. Fake model, torch and torchaudio; no model files needed."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

from helpers import PY, REPO

sys.path.insert(0, REPO)
import sing_notes  # noqa: E402

SING_CH, N = 11, 13


class Enc:
    """Stands in for the encoder output tensor: (B, channels, T, D), sliced on the channel axis."""
    def __init__(self, chans, ndim=4):
        self.chans, self.ndim = chans, ndim
        self.shape = (2, len(chans), 3, 4)[:ndim]

    def dim(self):
        return self.ndim

    def __getitem__(self, key):
        return Enc(self.chans[key[1]])


def fake_model(**over):
    tm = types.SimpleNamespace(program2channel_vocab={100: {"channel": SING_CH}}, num_decoding_channels=N)
    m = types.SimpleNamespace(task_manager=tm, decoder_type="multi-t5", decoder=types.SimpleNamespace(num_channels=N),
                              spectrogram=lambda x: x, pre_encoder=lambda x: x, embed_tokens=lambda x: x, lm_head=lambda x: x,
                              shift_right_fn=lambda x: x, max_total_token_length=9, pre_decoder=lambda x: x,
                              encoder=lambda inputs_embeds: {"last_hidden_state": Enc(list(range(N)))})
    for k, v in over.items():
        setattr(m, k, v)
    return m


class GuardTest(unittest.TestCase):
    def test_finds_the_singing_channel(self):
        self.assertEqual(sing_notes.singing_channel(fake_model()), SING_CH)

    def test_other_builds_get_none(self):
        tm_other = types.SimpleNamespace(program2channel_vocab={0: {"channel": 0}}, num_decoding_channels=N)
        for m in (fake_model(decoder_type="t5"), fake_model(decoder=types.SimpleNamespace(num_channels=1)),
                  fake_model(task_manager=tm_other), fake_model(lm_head=None)):
            self.assertIsNone(sing_notes.singing_channel(m))
        m = fake_model()
        del m.pre_decoder
        self.assertIsNone(sing_notes.singing_channel(m))


class SliceTest(unittest.TestCase):
    def test_decodes_one_channel(self):
        m, seen = fake_model(), {}

        def generate(**k):
            seen.update(chans=k["encoder_hidden_states"].chans, channels=m.decoder.num_channels, prefix=k["prefix_ids"])
            return "tokens"
        self.assertEqual(sing_notes.decode_channel(m, SING_CH, generate)("x"), "tokens")
        self.assertEqual(seen, {"chans": [SING_CH], "channels": 1, "prefix": None})
        self.assertEqual(m.decoder.num_channels, N)  # put back after the call

    def test_other_layout_raises(self):
        m = fake_model(encoder=lambda inputs_embeds: {"last_hidden_state": Enc(list(range(N)), ndim=3)})
        with self.assertRaises(sing_notes.OtherModel):
            sing_notes.decode_channel(m, SING_CH, lambda **k: "tokens")("x")
        self.assertEqual(m.decoder.num_channels, N)


# A fake YourMT3 code dir for main(): torch, torchaudio, model_helper and the amt/src modules sing_notes imports. Tokens
# carry the channels they came from; channel 11 gives one singing note, every other channel a piano note.
FAKES = {
    "torch.py": '''
import contextlib
load = None
def no_grad(): return contextlib.nullcontext()
def mean(a, dim): return a
def from_numpy(a): return a
''',
    "torchaudio.py": '''
import types
class A:
    def unsqueeze(self, d): return self
def load(uri): return A(), 44100
functional = types.SimpleNamespace(resample=lambda a, sr, to: a)
''',
    "model_helper.py": '''
import json, os, types
MODE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode.json")))
class Enc:
    def __init__(self, chans, ndim): self.chans, self.ndim = chans, ndim; self.shape = (2, len(chans), 3, 4)[:ndim]
    def dim(self): return self.ndim
    def __getitem__(self, k): return Enc(self.chans[k[1]], self.ndim)
class Tok:
    def __init__(self, chans): self.chans = chans
    def __getitem__(self, k): return self.chans[k[1]]
class Model:
    decoder_type = MODE.get("decoder_type", "multi-t5")
    audio_cfg = {"sample_rate": 16000, "input_frames": 32767}
    max_total_token_length = 9
    def __init__(self):
        self.task_manager = types.SimpleNamespace(program2channel_vocab={100: {"channel": 11}}, num_decoding_channels=13)
        self.decoder = types.SimpleNamespace(num_channels=13)
        self.spectrogram = self.pre_encoder = self.pre_decoder = self.embed_tokens = self.lm_head = self.shift_right_fn = lambda x: x
        self.encoder = lambda inputs_embeds: {"last_hidden_state": Enc(list(range(13)), MODE.get("ndim", 4))}
        self.task_manager.detokenize_list_batches = lambda cols, starts, return_events: (cols, None, None)
    def inference(self, x):
        return Tok(list(range(13)))
    def inference_file(self, bsz, audio_segments):
        json.dump({"bsz": bsz}, open(MODE["log"], "w"))
        return [self.inference("x")], None
def load_model_checkpoint(args, device):
    return Model()
''',
    "amt/src/model/t5mod_helper.py": '''
def task_cond_dec_generate(**k):
    import model_helper
    return model_helper.Tok(k["encoder_hidden_states"].chans)
''',
    "amt/src/utils/audio.py": '''
class S:
    shape = (1,)
    def astype(self, t): return self
    def unsqueeze(self, d): return self
def slice_padded_array(a, n, hop): return S()
''',
    "amt/src/utils/event2note.py": '''
import types
def merge_zipped_note_events_and_ties_to_notes(cols):
    return ([types.SimpleNamespace(onset=1.0, offset=2.0, pitch=60 if c == 11 else 40, program=100 if c == 11 else 0,
                                   is_drum=False) for c in cols],)
''',
    "amt/src/utils/note2event.py": '''
def mix_notes(per_ch): return [n for notes in per_ch for n in notes]
''',
}


class MainTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mlsingch-")
        for name, text in FAKES.items():
            p = os.path.join(self.d, "code", name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_main(self, **mode):
        log, out = os.path.join(self.d, "bsz.json"), os.path.join(self.d, "out.json")
        with open(os.path.join(self.d, "code", "mode.json"), "w") as f:
            json.dump(dict(mode, log=log), f)
        p = subprocess.run([PY, "-I", os.path.join(REPO, "sing_notes.py"), self.d, "in.wav", out], capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        with open(out) as f, open(log) as g:
            return json.load(f), json.load(g)["bsz"], p.stderr.decode()

    def test_singing_channel_only(self):
        self.assertEqual(self.run_main(), ([[1.0, 2.0, 60]], 2, ""))

    def test_other_builds_get_the_full_decode(self):
        for mode, said in ((dict(decoder_type="t5"), ""), (dict(ndim=3), "encoder output (2, 13, 3), not 13 channels")):
            rows, bsz, err = self.run_main(**mode)
            self.assertEqual((rows, bsz), ([[1.0, 2.0, 60]], 8))  # the same notes, the old way
            self.assertIn(said, err)
            self.assertIn("model structure differs; full decode", err)


if __name__ == "__main__":
    unittest.main()
