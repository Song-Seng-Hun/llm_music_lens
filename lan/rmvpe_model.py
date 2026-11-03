"""RMVPE vocal pitch estimation on CPU, run by rhythm_worker.py as its own process (torch stays out of the worker).

Usage: <python> -I rmvpe_model.py MODEL.pt STEM.wav OUT.npy
STEM.wav is PCM mono 16 kHz s16. OUT.npy holds f0 in Hz per 10 ms frame, 0 = unvoiced.

Network and decoding from RVC-Project Retrieval-based-Voice-Conversion-WebUI infer/rmvpe.py (MIT License, Copyright
(c) 2023 liujing04, 2023 源文雨, 2023 Ftps), reduced to CPU inference. RMVPE is by Wei, Cao, Dan and Chen, 2023
(arXiv 2306.15412; their code at github.com/Dream-High/RMVPE is Apache-2.0). The checkpoint is not part of this
repository; see README.md.

The audio runs in 30 s pieces with 2 s of context on each side: 600 s takes 2.6 GB instead of 17.2 GB, and 99.96% of
frames voiced in both stay within 50 cents of a whole-file run (measured 2026-11-03).
"""
import sys
import wave

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from librosa.filters import mel

SR, FRAME_HOP = 16000, 160
CHUNK_S, CONTEXT_S = 30, 2
THRESHOLD = 0.03


class BiGRU(nn.Module):
    def __init__(self, input_features, hidden_features, num_layers):
        super().__init__()
        self.gru = nn.GRU(input_features, hidden_features, num_layers=num_layers, batch_first=True, bidirectional=True)

    def forward(self, x):
        return self.gru(x)[0]


class ConvBlockRes(nn.Module):
    def __init__(self, in_channels, out_channels, momentum=0.01):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, (3, 3), (1, 1), (1, 1), bias=False),
            nn.BatchNorm2d(out_channels, momentum=momentum),
            nn.ReLU(),
            nn.Conv2d(out_channels, out_channels, (3, 3), (1, 1), (1, 1), bias=False),
            nn.BatchNorm2d(out_channels, momentum=momentum),
            nn.ReLU(),
        )
        if in_channels != out_channels:
            self.shortcut = nn.Conv2d(in_channels, out_channels, (1, 1))

    def forward(self, x):
        return self.conv(x) + (self.shortcut(x) if hasattr(self, "shortcut") else x)


class ResEncoderBlock(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, n_blocks=1, momentum=0.01):
        super().__init__()
        self.conv = nn.ModuleList([ConvBlockRes(in_channels, out_channels, momentum)])
        for _ in range(n_blocks - 1):
            self.conv.append(ConvBlockRes(out_channels, out_channels, momentum))
        self.kernel_size = kernel_size
        if kernel_size is not None:
            self.pool = nn.AvgPool2d(kernel_size=kernel_size)

    def forward(self, x):
        for conv in self.conv:
            x = conv(x)
        return x if self.kernel_size is None else (x, self.pool(x))


class Encoder(nn.Module):
    def __init__(self, in_channels, in_size, n_encoders, kernel_size, n_blocks, out_channels=16, momentum=0.01):
        super().__init__()
        self.bn = nn.BatchNorm2d(in_channels, momentum=momentum)
        self.layers = nn.ModuleList()
        for _ in range(n_encoders):
            self.layers.append(ResEncoderBlock(in_channels, out_channels, kernel_size, n_blocks, momentum=momentum))
            in_channels = out_channels
            out_channels *= 2
        self.out_channel = out_channels

    def forward(self, x):
        concat_tensors = []
        x = self.bn(x)
        for layer in self.layers:
            t, x = layer(x)
            concat_tensors.append(t)
        return x, concat_tensors


class Intermediate(nn.Module):
    def __init__(self, in_channels, out_channels, n_inters, n_blocks, momentum=0.01):
        super().__init__()
        self.layers = nn.ModuleList([ResEncoderBlock(in_channels, out_channels, None, n_blocks, momentum)])
        for _ in range(n_inters - 1):
            self.layers.append(ResEncoderBlock(out_channels, out_channels, None, n_blocks, momentum))

    def forward(self, x):
        for layer in self.layers:
            x = layer(x)
        return x


class ResDecoderBlock(nn.Module):
    def __init__(self, in_channels, out_channels, stride, n_blocks=1, momentum=0.01):
        super().__init__()
        out_padding = (0, 1) if stride == (1, 2) else (1, 1)
        self.conv1 = nn.Sequential(
            nn.ConvTranspose2d(in_channels, out_channels, (3, 3), stride, (1, 1), output_padding=out_padding, bias=False),
            nn.BatchNorm2d(out_channels, momentum=momentum),
            nn.ReLU(),
        )
        self.conv2 = nn.ModuleList([ConvBlockRes(out_channels * 2, out_channels, momentum)])
        for _ in range(n_blocks - 1):
            self.conv2.append(ConvBlockRes(out_channels, out_channels, momentum))

    def forward(self, x, concat_tensor):
        x = torch.cat((self.conv1(x), concat_tensor), dim=1)
        for conv2 in self.conv2:
            x = conv2(x)
        return x


class Decoder(nn.Module):
    def __init__(self, in_channels, n_decoders, stride, n_blocks, momentum=0.01):
        super().__init__()
        self.layers = nn.ModuleList()
        for _ in range(n_decoders):
            out_channels = in_channels // 2
            self.layers.append(ResDecoderBlock(in_channels, out_channels, stride, n_blocks, momentum))
            in_channels = out_channels

    def forward(self, x, concat_tensors):
        for i, layer in enumerate(self.layers):
            x = layer(x, concat_tensors[-1 - i])
        return x


class DeepUnet(nn.Module):
    def __init__(self, kernel_size, n_blocks, en_de_layers=5, inter_layers=4, in_channels=1, en_out_channels=16):
        super().__init__()
        self.encoder = Encoder(in_channels, 128, en_de_layers, kernel_size, n_blocks, en_out_channels)
        self.intermediate = Intermediate(self.encoder.out_channel // 2, self.encoder.out_channel, inter_layers, n_blocks)
        self.decoder = Decoder(self.encoder.out_channel, en_de_layers, kernel_size, n_blocks)

    def forward(self, x):
        x, concat_tensors = self.encoder(x)
        return self.decoder(self.intermediate(x), concat_tensors)


class E2E(nn.Module):
    def __init__(self, n_blocks, n_gru, kernel_size, en_de_layers=5, inter_layers=4, in_channels=1, en_out_channels=16):
        super().__init__()
        self.unet = DeepUnet(kernel_size, n_blocks, en_de_layers, inter_layers, in_channels, en_out_channels)
        self.cnn = nn.Conv2d(en_out_channels, 3, (3, 3), padding=(1, 1))
        self.fc = nn.Sequential(BiGRU(3 * 128, 256, n_gru), nn.Linear(512, 360), nn.Dropout(0.25), nn.Sigmoid())

    def forward(self, m):
        m = m.transpose(-1, -2).unsqueeze(1)
        return self.fc(self.cnn(self.unet(m)).transpose(1, 2).flatten(-2))


class Rmvpe:
    def __init__(self, model_path):
        self.model = E2E(4, 1, (2, 2))
        self.model.load_state_dict(torch.load(model_path, map_location="cpu", weights_only=True))  # no code in a checkpoint
        self.model.eval()
        self.mel_basis = torch.from_numpy(mel(sr=SR, n_fft=1024, n_mels=128, fmin=30, fmax=8000, htk=True)).float()
        self.window = torch.hann_window(1024)
        self.cents = np.pad(20 * np.arange(360) + 1997.3794084376191, (4, 4))

    def hidden(self, audio):
        fft = torch.stft(torch.from_numpy(audio).float().unsqueeze(0), n_fft=1024, hop_length=FRAME_HOP, win_length=1024,
                         window=self.window, center=True, return_complex=True)
        magnitude = torch.sqrt(fft.real.pow(2) + fft.imag.pow(2))
        m = torch.log(torch.clamp(torch.matmul(self.mel_basis, magnitude), min=1e-5))
        n_frames = m.shape[-1]
        n_pad = 32 * ((n_frames - 1) // 32 + 1) - n_frames
        if n_pad > 0:
            m = F.pad(m, (0, n_pad), mode="constant")
        with torch.no_grad():
            return self.model(m)[:, :n_frames].squeeze(0).numpy()

    def f0(self, audio):
        """Local average around each frame's peak over 9 bins; peak <= THRESHOLD is unvoiced."""
        salience = np.pad(self.hidden(audio), ((0, 0), (4, 4)))
        centre = np.argmax(salience, axis=1)
        rows = np.arange(len(salience))[:, None]
        bins = centre[:, None] + np.arange(-4, 5)
        weights = salience[rows, bins]
        cents = np.sum(weights * self.cents[bins], axis=1) / np.sum(weights, axis=1)
        cents[np.max(salience, axis=1) <= THRESHOLD] = 0
        out = 10 * 2 ** (cents / 1200)
        out[out == 10] = 0
        return out


def chunked_f0(f0, x, size=CHUNK_S, pad=CONTEXT_S, sr=SR, hop=FRAME_HOP):
    parts = []
    for a in range(0, len(x), size * sr):
        lo, hi = max(0, a - pad * sr), min(len(x), a + (size + pad) * sr)
        i = (a - lo) // hop
        parts.append(f0(x[lo:hi])[i:i + size * sr // hop])
    return np.concatenate(parts)


def main(argv):
    with wave.open(argv[2], "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    np.save(argv[3], chunked_f0(Rmvpe(argv[1]).f0, x))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
