"""BTC chord recognition on CPU, run by rhythm_worker.py as its own process (torch stays out of the worker itself).

Usage: <python> -I btc_model.py MODEL.pt IN.wav OUT.json
IN.wav is PCM mono 16 kHz s16. OUT.json holds {"hop_s": seconds per frame, "labels": [chord name per frame]}.

Network and features from jayg996/BTC-ISMIR19 (MIT License, Copyright (c) 2019 Jonggwon Park), reduced to CPU inference
with the large vocabulary. The method is by Park, Choi, Jeong and Lee (ISMIR 2019). The checkpoint is not part of this
repository; see README.md.

The training-only parts are gone: dropout, the loss and the attention maps. The LSTM stays because the checkpoint holds it.
"""
import json
import math
import sys
import wave

import librosa
import numpy as np
import numpy._core.multiarray as multiarray
import torch
import torch.nn as nn
import torch.nn.functional as F

SR, CHUNK_S, N_FRAMES = 22050, 10.0, 108
N_BINS, BINS_PER_OCTAVE, HOP = 144, 24, 2048
HIDDEN, HEADS, LAYERS = 128, 4, 8
ROOTS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
QUALITIES = ("m", "", "dim", "aug", "m6", "6", "m7", "mMaj7", "maj7", "7", "dim7", "m7b5", "sus2", "sus4")
LABELS = [r + q for r in ROOTS for q in QUALITIES] + ["X", "N"]  # X: unknown chord, N: no chord


def timing_signal(length, channels):
    n = channels // 2
    inv = np.exp(np.arange(n, dtype=np.float64) * -(math.log(1.0e4) / (n - 1)))
    t = np.arange(length)[:, None] * inv[None, :]
    return torch.from_numpy(np.concatenate([np.sin(t), np.cos(t)], axis=1)[None]).float()


class LayerNorm(nn.Module):
    def __init__(self, features, eps=1e-6):
        super().__init__()
        self.gamma = nn.Parameter(torch.ones(features))
        self.beta = nn.Parameter(torch.zeros(features))
        self.eps = eps

    def forward(self, x):
        return self.gamma * (x - x.mean(-1, keepdim=True)) / (x.std(-1, keepdim=True) + self.eps) + self.beta


class MultiHeadAttention(nn.Module):
    def __init__(self, bias_mask):
        super().__init__()
        self.scale = (HIDDEN // HEADS) ** -0.5
        self.bias_mask = bias_mask
        self.query_linear = nn.Linear(HIDDEN, HIDDEN, bias=False)
        self.key_linear = nn.Linear(HIDDEN, HIDDEN, bias=False)
        self.value_linear = nn.Linear(HIDDEN, HIDDEN, bias=False)
        self.output_linear = nn.Linear(HIDDEN, HIDDEN, bias=False)

    def split(self, x):
        b, t, d = x.shape
        return x.view(b, t, HEADS, d // HEADS).permute(0, 2, 1, 3)

    def forward(self, x):
        q, k, v = self.split(self.query_linear(x)) * self.scale, self.split(self.key_linear(x)), self.split(self.value_linear(x))
        logits = torch.matmul(q, k.permute(0, 1, 3, 2)) + self.bias_mask[:, :, :x.shape[1], :x.shape[1]]
        c = torch.matmul(F.softmax(logits, dim=-1), v).permute(0, 2, 1, 3).contiguous()
        return self.output_linear(c.view(x.shape[0], x.shape[1], HIDDEN))


class Conv(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv = nn.Conv1d(HIDDEN, HIDDEN, kernel_size=3)

    def forward(self, x):
        return self.conv(F.pad(x.permute(0, 2, 1), (1, 1))).permute(0, 2, 1)


class FeedForward(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.ModuleList([Conv(), Conv()])

    def forward(self, x):
        for layer in self.layers:
            x = F.relu(layer(x))
        return x


class AttentionBlock(nn.Module):
    def __init__(self, bias_mask):
        super().__init__()
        self.multi_head_attention = MultiHeadAttention(bias_mask)
        self.positionwise_convolution = FeedForward()
        self.layer_norm_mha = LayerNorm(HIDDEN)
        self.layer_norm_ffn = LayerNorm(HIDDEN)

    def forward(self, x):
        x = x + self.multi_head_attention(self.layer_norm_mha(x))
        return x + self.positionwise_convolution(self.layer_norm_ffn(x))


class BiAttention(nn.Module):
    def __init__(self):
        super().__init__()
        mask = torch.triu(torch.full((N_FRAMES, N_FRAMES), -math.inf), 1)[None, None]
        self.attn_block = AttentionBlock(mask)
        self.backward_attn_block = AttentionBlock(mask.transpose(2, 3))
        self.linear = nn.Linear(HIDDEN * 2, HIDDEN)

    def forward(self, x):
        return self.linear(torch.cat((self.attn_block(x), self.backward_attn_block(x)), dim=2))


class Encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.timing = timing_signal(N_FRAMES, HIDDEN)
        self.embedding_proj = nn.Linear(N_BINS, HIDDEN, bias=False)
        self.self_attn_layers = nn.Sequential(*[BiAttention() for _ in range(LAYERS)])
        self.layer_norm = LayerNorm(HIDDEN)

    def forward(self, x):
        return self.layer_norm(self.self_attn_layers(self.embedding_proj(x) + self.timing))


class Output(nn.Module):
    def __init__(self):
        super().__init__()
        self.output_projection = nn.Linear(HIDDEN, len(LABELS))
        self.lstm = nn.LSTM(input_size=HIDDEN, hidden_size=HIDDEN // 2, batch_first=True, bidirectional=True)

    def forward(self, x):
        return self.output_projection(x).argmax(-1)


class BTC(nn.Module):
    def __init__(self):
        super().__init__()
        self.self_attn_layers = Encoder()
        self.output_layer = Output()

    def forward(self, x):
        return self.output_layer(self.self_attn_layers(x))


def load(path):
    torch.serialization.add_safe_globals([(multiarray.scalar, "numpy.core.multiarray.scalar"), np.dtype,
                                          np.dtypes.Float64DType, np.dtypes.Float32DType])
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model = BTC()
    model.load_state_dict(ck["model"])
    model.eval()
    return model, float(ck["mean"]), float(ck["std"])


def features(x16):
    y = librosa.resample(x16, orig_sr=16000, target_sr=SR)
    n = int(SR * CHUNK_S)
    parts = []
    for i in range(0, len(y), n):
        if len(y[i:i + n]) >= 2:  # a single leftover sample has no CQT
            parts.append(librosa.cqt(y[i:i + n], sr=SR, n_bins=N_BINS, bins_per_octave=BINS_PER_OCTAVE, hop_length=HOP))
    return np.log(np.abs(np.concatenate(parts, axis=1)) + 1e-6).T


def predict(model, mean, std, feat):
    n = len(feat)
    x = np.pad((feat - mean) / std, ((0, -n % N_FRAMES), (0, 0)))
    x = torch.tensor(x, dtype=torch.float32)[None]
    with torch.no_grad():
        ids = torch.cat([model(x[:, i:i + N_FRAMES]) for i in range(0, x.shape[1], N_FRAMES)], dim=1)[0, :n].tolist()
    return [LABELS[i] for i in ids]


def main(model_path, wav_path, out_path):
    with wave.open(wav_path, "rb") as w:
        x16 = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    model, mean, std = load(model_path)
    labels = predict(model, mean, std, features(x16))
    with open(out_path, "w") as f:
        json.dump({"hop_s": CHUNK_S / N_FRAMES, "labels": labels}, f)


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
