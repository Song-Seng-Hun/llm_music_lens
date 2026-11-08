"""Music Lens roll page: one PNG of one sheet page (sheet.pages) of a stored rhythm result and its audio.

Usage: PYTHON -I roll.py RESULT.json AUDIO.wav PAGE OUT.png   (PYTHON has numpy and matplotlib: rhythm roll_python)
Top: spectrogram of AUDIO on a semitone axis (STFT 8192/256, 1/3-semitone rows, MIDI 24-96, 70 dB below the page
peak) with the result's note outlines over it, to check notes against real energy. Middle: piano roll by part
(melody, bass, other), beat lines (every 4th darker, counted from the first beat; not downbeats), chord labels.
Bottom: drum hits (hidden when the result has no drum hits). AUDIO is 16-bit PCM WAV, any rate and channel count.
Exit 0 with OUT written, 2 on bad arguments, 1 on any other failure; OUT is never left half-written.
Run by server.py / local_cli.py under limit_exec.py --roll; never imported by them.
"""
import json
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir; sheet.py is pure stdlib
import sheet  # noqa: E402

NFFT, HOP, MLO, MHI, RANGE_DB = 8192, 256, 24, 96, 70
LANES = (("melody_notes", "#ff5fd2", "melody"), ("bass_notes", "#ffb040", "bass"), ("other_notes", "#58d6ff", "other"))
DRUMS = ("kick", "snare", "hihat", "tom", "cymbal")
DRUM_COLORS = ("#ff4d4d", "#ffd84d", "#5cf2b0", "#b28cff", "#cccccc")


def spectrogram(np, path, t0, t1):
    """(times, semitone rows, dB) of [t0, t1] only; frames centred on t0 + j*HOP/sr."""
    with wave.open(path) as w:
        if w.getsampwidth() != 2:
            raise ValueError("not 16-bit PCM")
        sr, ch, total = w.getframerate(), w.getnchannels(), w.getnframes()
        s0, s1 = int(t0 * sr) - NFFT // 2, int(t1 * sr) + NFFT // 2
        if int(t0 * sr) >= total:
            raise ValueError("page outside the audio")
        w.setpos(max(0, s0))
        raw = w.readframes(min(s1, total) - max(0, s0))
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, ch).mean(1) / 32768
    x = np.pad(x, (max(0, -s0), max(0, s1 - total)))  # silence beyond the audio's ends
    n = (len(x) - NFFT) // HOP + 1
    freqs = np.fft.rfftfreq(NFFT, 1.0 / sr)
    rows = np.arange(MLO - 0.5, MHI + 0.5, 1 / 3)
    pos = np.interp(440 * 2 ** ((rows - 69) / 12), freqs, np.arange(len(freqs)))
    i0 = np.floor(pos).astype(int)
    i1, wt = np.minimum(i0 + 1, len(freqs) - 1), (pos - i0).astype(np.float32)
    win = np.hanning(NFFT).astype(np.float32)
    out = np.empty((len(rows), n), np.float32)
    for j in range(0, n, 256):  # chunks keep memory near 256 x NFFT floats
        idx = np.arange(j, min(n, j + 256))[:, None] * HOP + np.arange(NFFT)[None, :]
        s = np.abs(np.fft.rfft(x[idx] * win, axis=1))
        out[:, j:j + len(idx)] = (s[:, i0] * (1 - wt) + s[:, i1] * wt).T
    db = 20 * np.log10(out + 1e-6)
    return t0 + np.arange(n) * HOP / sr, rows, np.clip(db, db.max() - RANGE_DB, None)


def render(v, audio, page, out):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    p = sheet.pages(v)[page - 1]
    t0, t1 = p["start"], p["end"]
    times, rows, db = spectrogram(np, audio, t0, t1)
    beats = [b for b in v.get("beats") or [] if t0 <= b <= t1]
    first = sum(1 for b in v.get("beats") or [] if b < t0)  # beat count from the song's first beat
    notes = [(n, c, nm) for key, c, nm in LANES for n in v.get(key) or [] if n["end"] > t0 and n["start"] < t1]
    ms = [n["midi"] for n, _, _ in notes]
    lo, hi = max(MLO, (min(ms) if ms else 48) - 5), min(MHI, (max(ms) if ms else 72) + 5)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8})
    fig = plt.figure(figsize=(14, 7), facecolor="#141414")
    gs = fig.add_gridspec(3, 1, height_ratios=[3, 4, 0.8], hspace=0.04)
    axs, axr, axd = (fig.add_subplot(g) for g in gs)
    for ax in (axs, axr, axd):
        ax.set_facecolor("#1c1c1c")
        ax.set_xlim(t0, t1)
        ax.tick_params(colors="#aaaaaa")
        for sp in ax.spines.values():
            sp.set_color("#333")
        for i, b in enumerate(beats, first):
            ax.axvline(b, color="#555" if i % 4 == 0 else "#2e2e2e", lw=0.8 if i % 4 == 0 else 0.5, zorder=1)
    keep = (rows >= lo - 0.5) & (rows <= hi + 0.5)
    axs.imshow(db[keep], origin="lower", aspect="auto", cmap="magma", extent=[times[0], times[-1], lo - 0.5, hi + 0.5],
               zorder=0)
    for n, _, _ in notes:
        axs.add_patch(Rectangle((n["start"], n["midi"] - 0.5), n["end"] - n["start"], 1, fill=False, ec="#ffffff",
                                lw=0.6, zorder=3))
    for ax in (axs, axr):
        ax.set_ylim(lo - 0.5, hi + 0.5)
        ax.set_xticks([])
        cs = [k for k in range(lo, hi + 1) if k % 12 == 0]
        ax.set_yticks(cs, ["C%d" % (k // 12 - 1) for k in cs])
    for k in range(lo, hi + 1):
        if "#" in sheet.NAMES[k % 12]:
            axr.axhspan(k - 0.5, k + 0.5, color="#171717", zorder=0)
    for n, c, _ in notes:
        axr.add_patch(Rectangle((n["start"], n["midi"] - 0.4), n["end"] - n["start"], 0.8, fc=c, ec="none", zorder=3))
    for ch in v.get("chords") or []:
        if ch["end"] > t0 and ch["start"] < t1:
            axr.text(max(ch["start"], t0) + 0.03, hi + 0.1, ch.get("display") or ch["label"], color="#e0e0e0",
                     va="top", fontsize=8, zorder=4)
    if not v.get("drum_hits"):
        axd.set_visible(False)
    for h in v.get("drum_hits") or []:
        if t0 <= h["time"] <= t1:
            r = DRUMS.index(h["class"]) if h["class"] in DRUMS else len(DRUMS) - 1
            axd.plot(h["time"], r, "o", ms=3, color=DRUM_COLORS[r])
    axd.set_ylim(-0.7, len(DRUMS) - 0.3)
    axd.set_yticks(range(len(DRUMS)), DRUMS)
    axd.tick_params(labelsize=6)
    axd.set_xlabel("seconds", color="#aaaaaa")
    used = [nm for _, _, nm in LANES if any(x == nm for _, _, x in notes)]
    key = v.get("key") or {}
    head = "%s%s   %.1f-%.1f s   %s BPM (mlr)   key %s   notes: %s" % (
        p["section"] + " " if p["section"] else "", p["label"], t0, t1,
        "%.0f" % v["bpm"] if isinstance(v.get("bpm"), (int, float)) else "?",
        "%s %s" % (key["tonic"], key["scale"]) if "tonic" in key else "?", "  ".join(used) or "no notes")
    fig.text(0.125, 0.92, head, color="#eeeeee", fontsize=10)
    for i, (_, c, nm) in enumerate([lane for lane in LANES if lane[2] in used]):
        fig.text(0.70 + i * 0.07, 0.92, "■ " + nm, color=c, fontsize=9)
    tmp = out + ".part"
    try:
        with open(tmp, "xb") as f:
            fig.savefig(f, format="png", dpi=110, facecolor=fig.get_facecolor(), bbox_inches="tight")
        os.link(tmp, out)  # fails when OUT exists: never overwrite
    finally:
        plt.close(fig)
        if os.path.lexists(tmp):
            os.unlink(tmp)


def main(argv):
    if len(argv) != 5 or not argv[3].isdigit() or argv[3].startswith("0"):
        sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
        return 2
    with open(argv[1], "rb") as f:
        v = json.load(f)
    if not 1 <= int(argv[3]) <= len(sheet.pages(v)):
        sys.stderr.write("page out of range\n")
        return 2
    render(v, argv[2], int(argv[3]), argv[4])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("roll failed: %s\n" % type(e).__name__)
        sys.exit(1)
