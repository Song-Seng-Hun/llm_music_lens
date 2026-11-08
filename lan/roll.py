"""Music Lens roll page: one PNG of one sheet page (sheet.pages) of a stored rhythm result and its audio.

Usage: PYTHON -I roll.py RESULT.json AUDIO.wav PAGE OUT.png                 page   (PYTHON: numpy + matplotlib)
       PYTHON -I roll.py RESULT.json AUDIO.wav PAGE OUT.png MARKS.json      page with marks + their JSON
       PYTHON -I roll.py --poster RESULT.json AUDIO.wav START END OUT.png TITLE CREDIT   poster, <= 30 s

Page: top: spectrogram of AUDIO on a semitone axis (multi-resolution STFT, see BANDS; 1/3-semitone rows, MIDI 24-96,
70 dB below the page peak) with the result's note outlines over it, to check notes against real energy. Middle: piano
roll by part (melody, bass, other), bar lines at the result's downbeats (else every 4th beat, with a note), chords.
Bottom: drum hits (hidden when the result has no drum hits). AUDIO is 16-bit PCM WAV, any rate and channel count.
Marks (opt-in): suspect notes (weak, semitone, drum, quiet, reattackN) and missed harmonic energy, from a median-filter
harmonic/percussive split; thresholds are unvalidated rules of thumb. Poster (opt-in): 2400 x 1350 for people.
Exit 0 with OUT written, 2 on bad arguments, 1 on any other failure; OUT is never left half-written.
Run by server.py / local_cli.py under limit_exec.py --roll; never imported by them.
"""
import json
import math
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir; sheet.py is pure stdlib
import sheet  # noqa: E402

HOP, MLO, MHI, RANGE_DB = 256, 24, 96, 70
LANES = (("melody_notes", "#ff5fd2", "melody"), ("bass_notes", "#ffb040", "bass"), ("other_notes", "#58d6ff", "other"))
DRUMS = ("kick", "snare", "hihat", "tom", "cymbal")
DRUM_COLORS = ("#ff4d4d", "#ffd84d", "#5cf2b0", "#b28cff", "#cccccc")


# Multi-resolution STFT: each pitch band gets a longer window lower down, so two tones a semitone apart show as two
# peaks (a Hann main lobe's half width, 2 sr/N Hz, is under a semitone, 0.0595 f, from about MIDI 42 / 54 in the
# upper bands and from about MIDI 29 in the lowest; below 29 neighbours merge); frames share centres and hop, so
# the time axis matches the note boxes; magnitudes are divided by the window sum, so a steady tone reads the same
# level in every band. Low rows trade time detail for pitch detail (the 0.74 s window smears onsets by ~0.3 s).
BANDS = ((45, 0.743), (57, 0.372), (MHI + 1, 0.186))  # (below MIDI, window s): 32768 / 16384 / 8192 at 44.1 kHz


def smear(m):
    """Half the window (s) of the band pitch m is in: energy shows this long before an onset and after an end."""
    return next((sec for top, sec in BANDS if m < top), BANDS[-1][1]) / 2


def window(sr, sec):
    return 1 << max(8, int(round(math.log2(sec * sr))))


def stft_rows(np, path, a, b, short=0):
    """(sr, times, rows, M) for frames centred on a + j*HOP/sr up to b: M[row, frame] the multi-resolution magnitude
    on 1/3-semitone rows MIDI 24-96 (silence beyond the audio's ends); with short=N also S[frame, bin], a plain
    N-point STFT at the same centres (re-attack timing)."""
    with wave.open(path) as w:
        if w.getsampwidth() != 2:
            raise ValueError("not 16-bit PCM")
        sr, ch, total = w.getframerate(), w.getnchannels(), w.getnframes()
        if int(a * sr) >= total:
            raise ValueError("page outside the audio")
        half = max(window(sr, sec) for _, sec in BANDS) // 2
        s0, s1 = int(a * sr) - half, int(b * sr) + half
        w.setpos(max(0, s0))
        raw = w.readframes(min(s1, total) - max(0, s0))
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, ch).mean(1) / 32768
    x = np.pad(x, (max(0, -s0), max(0, s1 - total)))
    n = (int(b * sr) - int(a * sr)) // HOP + 1
    rows = MLO - 1 / 3 + np.arange(3 * (MHI - MLO + 1)) / 3  # centred on each semitone and its thirds, as drawn
    M = np.empty((len(rows), n), np.float32)
    lo = MLO - 1
    for top, sec in BANDS:
        sel = np.where((rows >= lo) & (rows < top))[0] if top <= MHI else np.where(rows >= lo)[0]
        lo = top
        N = window(sr, sec)
        freqs = np.fft.rfftfreq(N, 1.0 / sr)
        pos = np.interp(440 * 2 ** ((rows[sel] - 69) / 12), freqs, np.arange(len(freqs)))
        i0 = np.floor(pos).astype(int)
        i1, wt = np.minimum(i0 + 1, len(freqs) - 1), (pos - i0).astype(np.float32)
        win = np.hanning(N).astype(np.float32)
        win /= win.sum()
        step = max(16, (1 << 21) // N)  # frames per chunk: about 2M samples (8 MB float32) per chunk at any N
        for j in range(0, n, step):
            idx = np.arange(j, min(n, j + step))[:, None] * HOP + (half - N // 2) + np.arange(N)[None, :]
            s = np.abs(np.fft.rfft(x[idx] * win, axis=1))
            M[sel, j:j + len(idx)] = (s[:, i0] * (1 - wt) + s[:, i1] * wt).T
    times = int(a * sr) / sr + np.arange(n) * HOP / sr
    if not short:
        return sr, times, rows, M
    ws = np.hanning(short).astype(np.float32)
    S = np.empty((n, short // 2 + 1), np.float32)
    for j in range(0, n, 256):
        idx = np.arange(j, min(n, j + 256))[:, None] * HOP + (half - short // 2) + np.arange(short)[None, :]
        S[j:j + len(idx)] = np.abs(np.fft.rfft(x[idx] * ws, axis=1))
    return sr, times, rows, M, S


def spectrogram(np, path, t0, t1):
    """(times, semitone rows, dB) of [t0, t1] only, RANGE_DB below the page peak."""
    _, times, rows, M = stft_rows(np, path, t0, t1)
    db = 20 * np.log10(M + 1e-9)
    return times, rows, np.clip(db, db.max() - RANGE_DB, None)


def grid(v, t0, t1):
    """(lines [(time, bar start?)], note) inside [t0, t1]: bar lines at the stored bars' starts (downbeats) and the
    other beats lighter when the result has a bar layer; else every 4th beat counted from the first beat, said so."""
    beats = v.get("beats") or []
    rows = (sheet.done(v, "bars") or {}).get("rows") or []
    bars = sorted({r["start"] for r in rows} | {rows[-1]["end"]}) if rows else []
    if any(t0 <= t <= t1 for t in bars[:-1]):  # bars that end before the page (older results) leave it to the beats
        lines = [(t, True) for t in bars if t0 <= t <= t1]
        lines += [(t, False) for t in beats if t0 <= t <= t1 and all(abs(t - u) > 0.06 for u in bars)]
        bpb = sheet.meter(v)[0]
        return sorted(lines), "bar lines at downbeats" + (" (%d beats per bar)" % bpb if bpb else "")
    first = sum(1 for t in beats if t < t0)  # beat count from the song's first beat
    lines = [(t, i % 4 == 0) for i, t in enumerate([t for t in beats if t0 <= t <= t1], first)]
    return lines, "beats counted from the first beat (every 4th darker); no downbeats " + (
        "on this page" if bars else "in this result")


def draw_grid(axes, lines):
    for ax in axes:
        for t, bar in lines:
            ax.axvline(t, color="#555" if bar else "#2e2e2e", lw=0.8 if bar else 0.5, zorder=1)


def render(v, audio, page, out):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    p = sheet.pages(v)[page - 1]
    t0, t1 = p["start"], p["end"]
    times, rows, db = spectrogram(np, audio, t0, t1)
    lines, grid_note = grid(v, t0, t1)
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
    draw_grid((axs, axr, axd), lines)
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
    axr.text(0.005, 0.015, grid_note, transform=axr.transAxes, color="#bbbbbb", fontsize=7, va="bottom", zorder=5)
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


# ---- marks (opt-in): heuristic checks of the result's notes against the audio. Thresholds are unvalidated rules of
# thumb tuned by eye on a few clips; a flag means "look here", never a measured error rate.
MARGIN = 1.0  # s analysed on each side of the page, so notes and runs crossing its edges are judged on more frames
NFFT_SHORT = 2048  # re-attack timing
HARM = (0, 12, 19, 24, 28, 31)  # a note explains energy at these semitone offsets (harmonics 1-6)
WEAK_DB, SEMITONE_DB = 3.0, 6.0  # weak: row vs rows 2.5 semitones away; semitone: a row 1 semitone away is stronger
DRUM_ABOVE, QUIET_BELOW_DB = 0.25, 20.0  # vs the median percussive share / level of the page's notes
REATTACK_DB, REATTACK_LAG, REATTACK_GAP = 6.0, 0.03, 0.1
MISSED_LEVEL_DB, MISSED_PROMINENCE_DB, MISSED_MIN_S, MISSED_BRIDGE_S = 20.0, 12.0, 0.15, 0.05
MISSED_TAIL_S = 0.3  # after a note ends, its pitches (and harmonics) still ring: not missed for this long
MISSED_LOW_MIN_S = 0.4  # below BANDS[0][0] the 0.74 s window cannot place shorter runs: treat them as smear
MISSED_DRUM_S = 0.4, 0.2  # a run shorter than [0] within the smear of a drum hit (+[1] s after) is the drum
MISSED_NEIGHBOUR = 4  # rows (1/3 semitone) each side a missed peak is compared with


def name(m):
    return "%s%d" % (sheet.NAMES[m % 12], m // 12 - 1)


def analyse(np, path, t0, t1):
    """The multi-resolution rows (stft_rows) and a short NFFT_SHORT STFT at the same frame centres over
    [t0 - MARGIN, t1 + MARGIN] inside the audio, then a median-filter harmonic/percussive split."""
    from numpy.lib.stride_tricks import sliding_window_view as swv
    with wave.open(path) as w:
        dur = w.getnframes() / w.getframerate()
    sr, times, rows, M, S = stft_rows(np, path, max(0.0, t0 - MARGIN), min(dur, t1 + MARGIN), short=NFFT_SHORT)

    def med(a, k, axis):
        p = [(0, 0), (0, 0)]
        p[axis] = (k // 2, k // 2)
        return np.median(swv(np.pad(a, p, mode="edge"), k, axis=axis), axis=-1)
    H, P = med(M, int(round(0.2 * sr / HOP)) | 1, 1), med(M, 25, 0)  # ~0.2 s along time; 25 rows along pitch
    mask = H ** 2 / (H ** 2 + P ** 2 + 1e-30)
    Pm = M * (1 - mask)
    return {"sr": sr, "times": times, "rows": rows, "fs": np.fft.rfftfreq(NFFT_SHORT, 1.0 / sr), "M": M, "Pm": Pm,
            "Hdb": 20 * np.log10(M * mask + 1e-9), "S": S}


def marks(np, v, an, t0, t1):
    """(suspects, missed) for page [t0, t1]: notes overlapping it with flags weak/semitone/drum/quiet/reattackN, and
    harmonic energy no note (or its harmonics) explains, held >= MISSED_MIN_S."""
    tf, rows, Hdb, M, Pm, S, fs, sr = (an[k] for k in ("times", "rows", "Hdb", "M", "Pm", "S", "fs", "sr"))
    page = (tf >= t0) & (tf <= t1)
    ref = float(np.percentile(Hdb[:, page], 99.5))  # the page's level: thresholds are relative to it

    def near(m, half):
        return np.where(np.abs(rows - m) <= half + 1e-6)[0]
    lag, gap = max(1, int(round(REATTACK_LAG * sr / HOP))), int(round(REATTACK_GAP * sr / HOP))
    allnotes = [nt for key, _, _ in LANES for nt in v.get(key) or []]
    onsets = [(nt["start"], nt["midi"]) for nt in allnotes]
    stats = []
    for key, _, lane in LANES:
        for nt in v.get(key) or []:
            if not (nt["end"] > t0 and nt["start"] < t1):
                continue
            fm = (tf >= nt["start"]) & (tf <= nt["end"])
            if fm.sum() < 2 or len(near(nt["midi"], 1 / 3)) == 0:
                continue

            def level(m, frames=fm):
                return float(Hdb[near(m, 1 / 3)][:, frames].max(0).mean())
            on = level(nt["midi"])
            nb_r = np.concatenate([near(nt["midi"] - 2.5, 0.5), near(nt["midi"] + 2.5, 0.5)])
            contrast = on - (float(Hdb[nb_r][:, fm].mean()) if len(nb_r) else on)
            # a semitone neighbour is compared only on frames no other note (or its harmonics) at that pitch reaches,
            # widened by the band's smear: the long low windows spread a written neighbour into this note's span
            sm, side = smear(nt["midi"]), []
            for d in (-1, 1):
                nm = nt["midi"] + d
                if not MLO <= nm <= MHI:
                    continue
                keep = fm.copy()
                for o in allnotes:
                    if o is not nt and any(abs(o["midi"] + h - nm) < 0.5 for h in HARM):
                        keep &= ~((tf >= o["start"] - sm) & (tf <= o["end"] + sm))
                if keep.sum() >= 2:
                    side.append(level(nm, keep) - level(nt["midi"], keep))
            semi = max(side) if side else -99.0
            band = near(nt["midi"], 1)
            pr = float(Pm[band][:, fm].sum() / (M[band][:, fm].sum() + 1e-12))
            f0 = 440 * 2 ** ((nt["midi"] - 69) / 12)
            e = 20 * np.log10(S[fm][:, (fs >= f0 / 1.26) & (fs <= f0 * 2.5)].sum(1) + 1e-6)
            rises = np.where(e[lag:] - e[:-lag] > REATTACK_DB)[0] + lag
            rises = rises[rises > gap]  # the note's own attack
            # a rise where another note (any lane) with a harmonic in this band starts is that note, not a re-strike
            ft = tf[fm]
            rises = [i for i in rises if not any(abs(st - ft[i]) <= REATTACK_GAP and -4 <= m + h - nt["midi"] <= 16
                                                 for st, m in onsets if st > nt["start"] for h in HARM)]
            nre = int(1 + (np.diff(rises) > gap).sum()) if len(rises) else 0
            stats.append((lane, nt, on, contrast, semi, pr, nre))
    mid_on = float(np.median([x[2] for x in stats])) if stats else 0.0
    mid_pr = float(np.median([x[5] for x in stats])) if stats else 0.0
    suspects = []
    for lane, nt, on, contrast, semi, pr, nre in stats:
        flags = (["weak"] if contrast < WEAK_DB else []) + (["semitone"] if semi > SEMITONE_DB else []) + (
            ["drum"] if pr > mid_pr + DRUM_ABOVE else []) + (["quiet"] if on < mid_on - QUIET_BELOW_DB else []) + (
            ["reattack%d" % nre] if nre >= 2 else [])
        if flags:
            suspects.append({"start": nt["start"], "end": nt["end"], "lane": lane, "note": nt.get("note") or
                             name(nt["midi"]), "midi": nt["midi"], "flags": flags,
                             "contrast_db": round(contrast, 1), "semitone_db": round(semi, 1),
                             "perc_ratio": round(pr, 2), "level_db": round(on - ref, 1), "reattacks": nre})
    nbm = (np.roll(Hdb, MISSED_NEIGHBOUR, 0) + np.roll(Hdb, -MISSED_NEIGHBOUR, 0)) / 2
    pk = ((Hdb >= np.roll(Hdb, 1, 0)) & (Hdb >= np.roll(Hdb, -1, 0)) & (Hdb - nbm > MISSED_PROMINENCE_DB)
          & (Hdb > ref - MISSED_LEVEL_DB))
    pk[:8] = pk[-8:] = False
    semi = np.rint(rows).astype(int)
    cover = np.zeros((MHI + 40, len(tf)), bool)
    for nt in allnotes:
        for h in HARM:
            m = int(nt["midi"]) + h
            if m < cover.shape[0]:
                fm = (tf >= nt["start"] - 0.05 - smear(m)) & (tf <= nt["end"] + smear(m) + MISSED_TAIL_S)
                cover[max(0, m - 1):m + 2, fm] = True  # +-1 semitone slack
    grid = np.zeros((MHI + 1, len(tf)), bool)
    for r in np.where(pk.any(1))[0]:
        grid[semi[r]] |= pk[r]
    grid &= ~cover[:MHI + 1]
    minlen, bridge = int(round(MISSED_MIN_S * sr / HOP)), int(round(MISSED_BRIDGE_S * sr / HOP))
    lowlen = int(round(MISSED_LOW_MIN_S * sr / HOP))
    runs, hits = [], [h["time"] for h in v.get("drum_hits") or []]
    for m in range(MLO, MHI + 1):
        idx = np.where(grid[m])[0]
        if not len(idx):
            continue
        st = pv = idx[0]
        for i in list(idx[1:]) + [None]:
            if i is not None and i - pv <= bridge:
                pv = i
                continue
            a, b = float(tf[st]), float(tf[pv])
            if pv - st + 1 >= (lowlen if m < BANDS[0][0] else minlen) and not (b - a < MISSED_DRUM_S[0] and any(
                    h - smear(m) <= b and a <= h + smear(m) + MISSED_DRUM_S[1] for h in hits)):
                runs.append((m, a, b))
            if i is not None:
                st = pv = i
    missed = [{"start": round(a, 3), "end": round(b, 3), "midi": m, "note": name(m)} for m, a, b in sorted(runs, key=lambda r: (r[1], r[0]))
              if b > t0 and a < t1 and not any(o[0] == m - h and o[1] < b and o[2] > a for o in runs for h in HARM[1:])]
    return sorted(suspects, key=lambda s: (s["start"], s["midi"])), missed


def render_marks(v, audio, page, out, out_json):
    """The page with marks: harmonic part as the spectrogram, suspect notes red dashed (faded in the roll), missed
    energy green dotted, percussive energy behind the drum hits. Also writes {"page", "suspects", "missed"} JSON."""
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    p = sheet.pages(v)[page - 1]
    t0, t1 = p["start"], p["end"]
    an = analyse(np, audio, t0, t1)
    sus, missed = marks(np, v, an, t0, t1)
    bad = {(s["lane"], s["start"], s["midi"]) for s in sus}
    tf, rows, Hdb = an["times"], an["rows"], an["Hdb"]
    lines, grid_note = grid(v, t0, t1)
    notes = [(n, c, nm) for key, c, nm in LANES for n in v.get(key) or [] if n["end"] > t0 and n["start"] < t1]
    ms = [n["midi"] for n, _, _ in notes] + [r["midi"] for r in missed]
    lo, hi = max(MLO, (min(ms) if ms else 48) - 5), min(MHI, (max(ms) if ms else 72) + 5)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8})
    fig = plt.figure(figsize=(14, 7.6), facecolor="#141414")
    gs = fig.add_gridspec(3, 1, height_ratios=[3, 4, 1.0], hspace=0.04)
    axs, axr, axd = (fig.add_subplot(g) for g in gs)
    for ax in (axs, axr, axd):
        ax.set_facecolor("#1c1c1c")
        ax.set_xlim(t0, t1)
        ax.tick_params(colors="#aaaaaa")
        for sp in ax.spines.values():
            sp.set_color("#333")
    draw_grid((axs, axr, axd), lines)
    m = (tf >= t0) & (tf <= t1)
    keep = (rows >= lo - 0.5) & (rows <= hi + 0.5)
    img = Hdb[keep][:, m]
    vmax = float(np.percentile(img, 99.5))
    axs.imshow(img, origin="lower", aspect="auto", cmap="magma", vmin=vmax - 50, vmax=vmax,
               extent=[tf[m][0], tf[m][-1], lo - 0.5, hi + 0.5], zorder=0)
    for n, c, nm in notes:
        b = (nm, n["start"], n["midi"]) in bad
        axs.add_patch(Rectangle((n["start"], n["midi"] - 0.5), n["end"] - n["start"], 1, fill=False,
                                ec="#ff3b3b" if b else "#ffffff", lw=1.0 if b else 0.6, ls="--" if b else "-", zorder=3))
        axr.add_patch(Rectangle((n["start"], n["midi"] - 0.4), n["end"] - n["start"], 0.8, fc=c, alpha=0.3 if b else 1,
                                ec="#ff3b3b" if b else "none", lw=1.4, ls="--", zorder=3))
    for r in missed:
        if lo - 0.5 <= r["midi"] <= hi + 0.5:
            for ax, h in ((axs, 1), (axr, 0.8)):
                ax.add_patch(Rectangle((r["start"], r["midi"] - h / 2), r["end"] - r["start"], h, fill=False,
                                       ec="#7dff5a", lw=1.2, ls=":", hatch="///" if ax is axr else None, zorder=4))
    axs.text(0.005, 0.97, "harmonic part (sharp hits reduced; pitched drum decay stays)", transform=axs.transAxes,
             color="#bbbbbb", fontsize=7, va="top")
    for ax in (axs, axr):
        ax.set_ylim(lo - 0.5, hi + 0.5)
        ax.set_xticks([])
        cs = [k for k in range(lo, hi + 1) if k % 12 == 0]
        ax.set_yticks(cs, ["C%d" % (k // 12 - 1) for k in cs])
    for k in range(lo, hi + 1):
        if "#" in sheet.NAMES[k % 12]:
            axr.axhspan(k - 0.5, k + 0.5, color="#171717", zorder=0)
    for ch in v.get("chords") or []:
        if ch["end"] > t0 and ch["start"] < t1:
            axr.text(max(ch["start"], t0) + 0.03, hi + 0.1, ch.get("display") or ch["label"], color="#e0e0e0",
                     va="top", fontsize=8, zorder=4)
    pc = an["Pm"][:, m].sum(0)
    axd.fill_between(tf[m], -0.7, pc / (pc.max() + 1e-12) * 4.6 - 0.7, color="#3a5a7a", alpha=0.6, lw=0, zorder=0)
    for h in v.get("drum_hits") or []:
        if t0 <= h["time"] <= t1:
            r = DRUMS.index(h["class"]) if h["class"] in DRUMS else len(DRUMS) - 1
            axd.plot(h["time"], r, "o", ms=3, color=DRUM_COLORS[r], zorder=3)
    axd.set_ylim(-0.7, len(DRUMS) - 0.3)
    axd.set_yticks(range(len(DRUMS)), DRUMS)
    axd.tick_params(labelsize=6)
    axd.text(0.005, 0.92, "percussive energy" + ("" if v.get("drum_hits") else " (no drum layer in the result)"),
             transform=axd.transAxes, color="#bbbbbb", fontsize=7, va="top")
    axd.set_xlabel("seconds   (marks: unvalidated heuristics, not accuracy)", color="#aaaaaa")
    axr.text(0.005, 0.015, grid_note, transform=axr.transAxes, color="#bbbbbb", fontsize=7, va="bottom", zorder=5)
    used = [nm for _, _, nm in LANES if any(x == nm for _, _, x in notes)]
    nbad = sum(1 for n, _, nm in notes if (nm, n["start"], n["midi"]) in bad)
    fig.text(0.125, 0.92, "%s%s   %.1f-%.1f s   suspect (red dashed) %d/%d   missed (green dotted) %d" % (
        p["section"] + " " if p["section"] else "", p["label"], t0, t1, nbad, len(notes), len(missed)),
        color="#eeeeee", fontsize=10)
    for i, (_, c, nm) in enumerate([lane for lane in LANES if lane[2] in used]):
        fig.text(0.74 + i * 0.06, 0.92, "■ " + nm, color=c, fontsize=9)
    save(plt, fig, out, dpi=110, bbox_inches="tight")
    with open(out_json, "x") as f:
        json.dump({"page": p, "suspects": sus, "missed": missed}, f, separators=(",", ":"))


# ---- poster (opt-in): one span as a 2400 x 1350 image for people
POSTER = dict(bg="#05040a", fg="#ffffff", sub="#a99fc7", grid="#1c1830", wk="#cfc8e8", bk="#120f1e",
              col={"melody": "#ff4fd8", "bass": "#ffc14d", "harmony": "#4dfcff"})
POSTER_MAX = 30.0  # s, as one zoom
POSTER_MIN = 0.5  # s; shorter spans have too few frames to draw
SPAN_EPS = 1e-6  # float slack: 2.02..32.02 is 30 s (the server checks round(end - start, 6))
TEXT_MAX = 80  # characters of title and credit


def text_ok(s):
    return len(s) <= TEXT_MAX and s.isprintable()


def render_poster(v, audio, t0, t1, out, title, credit):
    """Glowing note lines (width from the semitone height, alpha from the note's harmonic energy) over a faint
    harmonic spectrogram, keyboard strip, chord band, kick/snare ticks, title, credit; legend only for lanes with
    >= 0.5 s visible; the low range reaches down to strong sustained energy with no note (e.g. a bass pedal)."""
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    an = analyse(np, audio, t0, t1)
    tf, rows, Hdb = an["times"], an["rows"], an["Hdb"]
    m = (tf >= t0) & (tf <= t1)
    lanes = (("melody_notes", "melody"), ("bass_notes", "bass"), ("other_notes", "harmony"))
    notes = [(n, nm) for k, nm in lanes for n in v.get(k) or [] if n["end"] > t0 and n["start"] < t1
             and MLO <= n["midi"] <= MHI]
    ms = [n["midi"] for n, _ in notes] or [48, 72]
    lo, hi = max(MLO, min(ms) - 3), min(MHI, max(ms) + 3)
    W = Hdb[:, m]
    strong = np.where(np.percentile(W, 75, axis=1) > np.percentile(W, 99.5) - 22)[0]
    if len(strong):
        lo = max(MLO, min(lo, int(np.floor(rows[strong[0]])) - 2))
    keep = (rows >= lo - 0.5) & (rows <= hi + 0.5)
    img = Hdb[keep][:, m]
    vmax = float(np.percentile(img, 99.5))

    def energy(n):
        f = (tf >= n["start"]) & (tf <= n["end"])
        return float(Hdb[np.abs(rows - n["midi"]) <= 0.34][:, f].max(0).mean()) if f.sum() else vmax - 40
    alpha = [min(1.0, max(0.0, (energy(n) - (vmax - 40)) / 40)) * 0.6 + 0.4 for n, _ in notes]
    plt.rcParams.update({"font.family": "DejaVu Sans"})
    fig = plt.figure(figsize=(16, 9), facecolor=POSTER["bg"])
    L, R = 0.075, 0.975
    axr = fig.add_axes([L, 0.10, R - L, 0.70])
    axc = fig.add_axes([L, 0.81, R - L, 0.04])
    axk = fig.add_axes([L - 0.028, 0.10, 0.026, 0.70])
    for ax in (axr, axc, axk):
        ax.set_facecolor(POSTER["bg"])
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
    for ax in (axr, axc):
        ax.set_xlim(t0, t1)
    axr.imshow(img, origin="lower", aspect="auto", cmap="inferno", vmin=vmax - 50, vmax=vmax, alpha=0.55,
               interpolation="bilinear", extent=[tf[m][0], tf[m][-1], lo - 0.5, hi + 0.5])
    for t, bar in grid(v, t0, t1)[0]:
        axr.axvline(t, color=POSTER["grid"], lw=1.2 if bar else 0.5, alpha=1 if bar else 0.6, zorder=1)
    axr.set_ylim(lo - 0.5, hi + 0.5)
    fig.canvas.draw()
    bb = axr.get_window_extent()
    pt = bb.height / (hi - lo + 1) * 72 / fig.dpi  # one semitone in points
    cap = 0.5 * pt * 0.8 * (t1 - t0) / (bb.width * 72 / fig.dpi)  # a round cap's length in seconds
    for (n, nm), a in zip(notes, alpha):
        c = POSTER["col"][nm]
        xa = max(n["start"], t0) + cap
        xb = max(xa, min(n["end"], t1) - cap)
        for k, ga in ((3.2, 0.07), (2.0, 0.14), (1.35, 0.3)):
            axr.plot([xa, xb], [n["midi"]] * 2, color=c, lw=pt * 0.8 * k, alpha=ga * a, solid_capstyle="round", zorder=3)
        axr.plot([xa, xb], [n["midi"]] * 2, color=c, lw=pt * 0.8, alpha=a, solid_capstyle="round", zorder=4)
        axr.plot([xa, xb], [n["midi"]] * 2, color="white", lw=pt * 0.22, alpha=0.55 * a, solid_capstyle="round", zorder=5)
    axk.set_ylim(lo - 0.5, hi + 0.5)
    axk.set_xlim(0, 1)
    for k in range(lo, hi + 1):
        axk.add_patch(Rectangle((0, k - 0.5), 1, 1, fc=POSTER["wk"], ec=POSTER["bg"], lw=0.4))
    for k in range(lo, hi + 1):
        if "#" in sheet.NAMES[k % 12]:
            axk.add_patch(Rectangle((0.38, k - 0.45), 0.62, 0.9, fc=POSTER["bk"], ec="none", zorder=2))
        if k % 12 == 0:
            fig.text(L - 0.032, axk.transData.transform((0, k))[1] / fig.bbox.height, "C%d" % (k // 12 - 1),
                     color=POSTER["sub"], fontsize=9, ha="right", va="center")
    axc.set_ylim(0, 1)
    for i, c in enumerate([c for c in v.get("chords") or [] if c["end"] > t0 and c["start"] < t1]):
        x0, x1 = max(c["start"], t0), min(c["end"], t1)
        axc.add_patch(Rectangle((x0, 0.1), x1 - x0, 0.8, fc=POSTER["grid"], alpha=0.5 if i % 2 else 0.25, ec="none"))
        if x1 - x0 > 0.25:
            axc.text(x0 + 0.06, 0.5, c.get("display") or c["label"], color=POSTER["fg"], fontsize=11, va="center",
                     fontweight="bold")
    for h in v.get("drum_hits") or []:
        y = {"kick": 0.04, "snare": 0.065}.get(h["class"])
        if y and t0 <= h["time"] <= t1:
            x = L + (h["time"] - t0) / (t1 - t0) * (R - L)
            fig.add_artist(plt.Line2D([x, x], [y, y + 0.015], color=POSTER["sub"], lw=2 if h["class"] == "kick" else 1,
                                      alpha=0.8))
    if v.get("drum_hits"):  # no lane labels over an empty lane
        for y, nm in ((0.052, "kick"), (0.077, "snare")):
            fig.text(L - 0.004, y, nm, color=POSTER["sub"], fontsize=8, ha="right", va="center")
    ty = 0.93
    fig.text(L, ty, title, color=POSTER["fg"], fontsize=26, fontweight="bold", va="center", parse_math=False)
    key = v.get("key") or {}
    fig.text(L, ty - 0.045, "%s BPM  ·  %s  ·  %.0f–%.0f s   |   spectrogram + transcribed notes by Music Lens" % (
        "%.0f" % v["bpm"] if isinstance(v.get("bpm"), (int, float)) else "?",
        "%s %s" % (key["tonic"], key["scale"]) if "tonic" in key else "key ?", t0, t1),
        color=POSTER["sub"], fontsize=12, va="center")
    dur, xr = {}, R
    for n, nm in notes:
        dur[nm] = dur.get(nm, 0) + min(n["end"], t1) - max(n["start"], t0)
    for nm in reversed([nm for _, nm in lanes if dur.get(nm, 0) >= 0.5]):  # only lanes one can see
        tx = fig.text(xr, ty, "● " + nm, color=POSTER["col"][nm], fontsize=13, va="center", ha="right")
        fig.canvas.draw()
        xr = tx.get_window_extent().x0 / fig.bbox.width - 0.02
    fig.text(R, 0.015, credit, color=POSTER["sub"], fontsize=8, ha="right", parse_math=False)  # "$5 or $6" stays text
    save(plt, fig, out, dpi=150)


def save(plt, fig, out, **kw):
    tmp = out + ".part"
    try:
        with open(tmp, "xb") as f:
            fig.savefig(f, format="png", facecolor=fig.get_facecolor(), **kw)
        os.link(tmp, out)  # fails when OUT exists: never overwrite
    finally:
        plt.close(fig)
        if os.path.lexists(tmp):
            os.unlink(tmp)


def parse(argv):
    """(function, args) for a valid argv, else None. Validation only; rendering errors are not usage errors."""
    try:
        if len(argv) == 9 and argv[1] == "--poster":
            with open(argv[2], "rb") as f:
                v = json.load(f)
            t0, t1 = float(argv[4]), float(argv[5])
            if (0 <= t0 and t1 <= v["duration"] + SPAN_EPS and POSTER_MIN - SPAN_EPS <= t1 - t0 <= POSTER_MAX + SPAN_EPS
                    and text_ok(argv[7]) and text_ok(argv[8])):
                return render_poster, (v, argv[3], t0, t1, argv[6], argv[7], argv[8])
        elif len(argv) in (5, 6) and argv[3].isdigit() and not argv[3].startswith("0"):
            with open(argv[1], "rb") as f:
                v = json.load(f)
            if 1 <= int(argv[3]) <= len(sheet.pages(v)):
                return (render_marks, (v, argv[2], int(argv[3]), argv[4], argv[5])) if len(argv) == 6 else (
                    render, (v, argv[2], int(argv[3]), argv[4]))
    except ValueError:
        pass
    return None


def main(argv):
    job = parse(argv)
    if job is None:
        sys.stderr.write("\n".join(__doc__.split("\n\n")[1].splitlines()[:3]) + "\n")
        return 2
    job[0](*job[1])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("roll failed: %s\n" % type(e).__name__)
        sys.exit(1)
