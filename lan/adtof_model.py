"""ADTOF Frame_RNN drum transcription on CPU, run by rhythm_worker.py as its own process (torch stays out of the worker itself).

Usage: <python> -I adtof_model.py CHECKPOINT.index IN.wav OUT.json
CHECKPOINT.data-00000-of-00001 sits next to the index. OUT.json holds {class name: [hit times in seconds]}.

The method is by Zehren, Alunno and Bientinesi (ADTOF, ISMIR 2021; Signals 2023). This file is a separate implementation
from the checkpoint's variable shapes and the layer output sizes and parameters published by ADTOF-pytorch (xavriley), with
no code from ADTOF or ADTOF-pytorch. The filterbank and peak picking follow madmom (BSD 2-Clause, Copyright (c) 2012-2014
Department of Computational Perception, Johannes Kepler University, Linz, Austria and Austrian Research Institute for
Artificial Intelligence (OFAI), Vienna, Austria). The checkpoint is CC BY-NC-SA 4.0 and is not part of this repository; see
README.md. It is read as raw tensors, so TensorFlow is not needed and nothing in it can run.

madmom notice for filterbank() and pick():
Copyright (c) 2012-2014 Department of Computational Perception, Johannes Kepler University, Linz, Austria and Austrian
Research Institute for Artificial Intelligence (OFAI), Vienna, Austria. All rights reserved.
Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following
conditions are met:
1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following
   disclaimer in the documentation and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING,
BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT
SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
import json
import struct
import sys

import librosa
import numpy as np
import scipy.ndimage
import torch
import torch.nn as nn
import torch.nn.functional as F

SR, FPS, FRAME, BANDS_PER_OCTAVE, FMIN, FMAX = 44100, 100, 2048, 12, 20.0, 20000.0
CLASSES = ("kick", "snare", "tom", "hihat", "cymbal")
THRESHOLDS = (0.22, 0.24, 0.32, 0.22, 0.30)
PRE_AVG, POST_AVG, PRE_MAX, POST_MAX, COMBINE = 10, 1, 2, 1, 2  # frames at 100 per second
GRU_UNITS = 60
SUFFIX = "/.ATTRIBUTES/VARIABLE_VALUE"


def varint(b, i):
    value = shift = 0
    while True:
        c = b[i]
        i += 1
        value |= (c & 127) << shift
        shift += 7
        if c < 128:
            return value, i


def table_block(buf, offset, size):
    """Key and value of each entry in one block of the index, which is a LevelDB table."""
    b = buf[offset:offset + size]
    restarts = struct.unpack("<I", b[-4:])[0]
    end, i, key = len(b) - 4 - 4 * restarts, 0, b""
    while i < end:
        shared, i = varint(b, i)
        fresh, i = varint(b, i)
        length, i = varint(b, i)
        key = key[:shared] + b[i:i + fresh]
        i += fresh
        yield key, b[i:i + length]
        i += length


def bundle_entry(v):
    """dtype, shape, offset and size from a BundleEntryProto."""
    i, out = 0, {"dtype": 0, "shape": (), "offset": 0, "size": 0}
    while i < len(v):
        tag, i = varint(v, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            x, i = varint(v, i)
            if field in (1, 4, 5):
                out[{1: "dtype", 4: "offset", 5: "size"}[field]] = x
        elif wire == 2:
            n, i = varint(v, i)
            if field == 2:
                j, dims, shape = 0, [], v[i:i + n]
                while j < n:
                    tag2, j = varint(shape, j)
                    m, j = varint(shape, j)
                    if tag2 >> 3 == 2:
                        dims.append(varint(shape[j:j + m], 1)[0])
                    j += m
                out["shape"] = tuple(dims)
            i += n
        elif wire == 5:
            i += 4
        else:
            raise ValueError("unexpected wire type in checkpoint index")
    return out


def read_checkpoint(index_path):
    """{variable name: float32 array} for every model variable; optimizer slots stay out."""
    with open(index_path, "rb") as f:
        index = f.read()
    if index[-8:] != bytes.fromhex("57fb808b247547db"):
        raise ValueError("not a checkpoint index")
    _, i = varint(index, len(index) - 48)
    _, i = varint(index, i)
    offset, i = varint(index, i)
    size, i = varint(index, i)
    with open(index_path[:-len(".index")] + ".data-00000-of-00001", "rb") as f:
        data = f.read()
    out = {}
    for _, handle in table_block(index, offset, size):
        o, j = varint(handle, 0)
        z, j = varint(handle, j)
        if index[o + z] != 0:
            raise ValueError("compressed checkpoint index")
        for key, value in table_block(index, o, z):
            name = key.decode()
            if name.endswith(SUFFIX) and "OPTIMIZER" not in name:
                e = bundle_entry(value)
                if e["dtype"] == 1:
                    out[name[:-len(SUFFIX)]] = np.frombuffer(data, "<f4", e["size"] // 4, e["offset"]).reshape(e["shape"]).copy()
    return out


def filterbank():
    """Overlapping triangles on the FFT bins nearest the 12-per-octave frequencies tuned to A4, each summing to 1."""
    bin_hz = np.fft.fftfreq(FRAME, 1.0 / SR)[:FRAME // 2]
    first = int(np.floor(np.log2(FMIN / 440.0) * BANDS_PER_OCTAVE))
    last = int(np.ceil(np.log2(FMAX / 440.0) * BANDS_PER_OCTAVE))
    hz = 440.0 * 2.0 ** (np.arange(first, last) / BANDS_PER_OCTAVE)
    hz = hz[(hz >= FMIN) & (hz <= FMAX)]
    right = np.clip(np.searchsorted(bin_hz, hz), 1, len(bin_hz) - 1)
    bins = np.unique(np.where(hz - bin_hz[right - 1] < bin_hz[right] - hz, right - 1, right))
    bank = np.zeros((len(bin_hz), len(bins) - 2), np.float32)
    for n, (a, c, b) in enumerate(zip(bins, bins[1:], bins[2:])):
        if b - a < 2:
            c, b = a, a + 1
        tri = np.zeros(b - a, np.float32)
        tri[:c - a] = np.linspace(0, 1, c - a, endpoint=False)
        tri[c - a:] = np.linspace(1, 0, b - c, endpoint=False)
        bank[a:b, n] = tri / max(float(tri.sum()), 1e-9)
    return bank


def spectrogram(y):
    """log10(1 + filtered magnitude) with frames centred on every hop."""
    hop, window = SR // FPS, np.hanning(FRAME).astype(np.float32)
    frames = int(np.ceil(len(y) / hop))
    pad = np.concatenate([np.zeros(FRAME // 2, np.float32), y, np.zeros(FRAME, np.float32)])
    bank, out = filterbank(), []
    for start in range(0, frames, 2000):
        idx = (np.arange(start, min(start + 2000, frames)) * hop)[:, None] + np.arange(FRAME)
        out.append(np.log10(1.0 + np.abs(np.fft.rfft(pad[idx] * window, axis=1))[:, :FRAME // 2].astype(np.float32) @ bank))
    return np.concatenate(out) if out else np.zeros((0, bank.shape[1]), np.float32)


class Network(nn.Module):
    """Two blocks of convolution, activation and batch norm over time and frequency, then three bidirectional GRUs."""

    def __init__(self, channels=(32, 32, 64, 64)):
        super().__init__()
        ins = (1,) + channels[:-1]
        self.convs = nn.ModuleList(nn.Conv2d(a, b, 3, padding=1) for a, b in zip(ins, channels))
        self.norms = nn.ModuleList(nn.BatchNorm2d(b, eps=1e-3) for b in channels)
        self.grus = nn.ModuleList(nn.GRU(640 if i == 0 else 120, GRU_UNITS, batch_first=True, bidirectional=True) for i in range(3))
        self.out = nn.Linear(120, len(CLASSES))

    def forward(self, x):
        x = x[:, None]  # batch, channel, time, frequency
        for i in range(4):
            x = self.norms[i](F.relu(self.convs[i](x)))
            if i % 2:
                need = -x.shape[3] % 3  # the checkpoint's pooling keeps a partial window, split around the edges
                x = F.pad(x, (need // 2, need - need // 2), value=-torch.inf) if need else x
                x = F.max_pool2d(x, (1, 3))
        x = x.permute(0, 2, 3, 1).flatten(2)  # frequency first, channels within it, as the checkpoint was trained
        for gru in self.grus:
            x = gru(x)[0]
        return torch.sigmoid(self.out(x))


def load_gru(gru, forward, backward):
    """The checkpoint orders each GRU's three parts update, reset, new; torch orders them reset, update, new."""
    for tag, v in (("l0", forward), ("l0_reverse", backward)):
        for name, w in (("ih", v["kernel"]), ("hh", v["recurrent_kernel"])):
            z, r, n = np.split(w, 3, axis=1)
            getattr(gru, f"weight_{name}_{tag}").data = torch.from_numpy(np.concatenate([r, z, n], 1).T.copy())
        for name, b in (("ih", v["bias"][0]), ("hh", v["bias"][1])):
            z, r, n = np.split(b, 3)
            getattr(gru, f"bias_{name}_{tag}").data = torch.from_numpy(np.concatenate([r, z, n]).copy())


def load_network(index_path):
    w = read_checkpoint(index_path)
    net = Network()
    for i in range(4):
        conv, norm = f"layer_with_weights-0/layer_with_weights-{2 * i}", f"layer_with_weights-0/layer_with_weights-{2 * i + 1}"
        net.convs[i].weight.data = torch.from_numpy(w[conv + "/kernel"].transpose(3, 2, 0, 1).copy())
        net.convs[i].bias.data = torch.from_numpy(w[conv + "/bias"])
        for ours, theirs in (("weight", "gamma"), ("bias", "beta"), ("running_mean", "moving_mean"), ("running_var", "moving_variance")):
            getattr(net.norms[i], ours).data = torch.from_numpy(w[f"{norm}/{theirs}"])
    for i in range(3):
        parts = [{k: w[f"layer_with_weights-{i + 1}/{side}_layer/cell/{k}"] for k in ("kernel", "recurrent_kernel", "bias")} for side in ("forward", "backward")]
        load_gru(net.grus[i], *parts)
    net.out.weight.data = torch.from_numpy(w["layer_with_weights-4/kernel"].T.copy())
    net.out.bias.data = torch.from_numpy(w["layer_with_weights-4/bias"])
    return net.eval()


def pick(activation, threshold):
    """Frames above the local average plus the threshold that are local maxima; a peak within 20 ms of the last one is dropped."""
    avg = scipy.ndimage.uniform_filter1d(activation, PRE_AVG + POST_AVG + 1, mode="constant", origin=(PRE_AVG - POST_AVG) // 2)
    kept = activation * (activation >= avg + threshold)
    top = scipy.ndimage.maximum_filter1d(kept, PRE_MAX + POST_MAX + 1, mode="constant", origin=(PRE_MAX - POST_MAX) // 2)
    out = []
    for frame in np.nonzero(kept * (kept == top))[0]:
        if not out or frame - out[-1] > COMBINE:
            out.append(int(frame))
    return [f / FPS for f in out]


def transcribe(net, path):
    y = librosa.load(path, sr=SR, mono=True)[0]
    with torch.no_grad():
        active = net(torch.from_numpy(spectrogram(y))[None])[0].numpy()
    return {c: pick(active[:, i], THRESHOLDS[i]) for i, c in enumerate(CLASSES)}


if __name__ == "__main__":
    index_path, wav, out_path = sys.argv[1:4]
    with open(out_path, "w") as f:
        json.dump(transcribe(load_network(index_path), wav), f)
