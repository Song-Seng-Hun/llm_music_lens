"""Music Lens sheet view: one offline HTML file of a whole stored rhythm result with its audio.

Usage: PYTHON -I view.py RESULT.json AUDIO.wav AUDIO.m4a OUT.html [--marks]      (PYTHON: numpy, matplotlib, Pillow)

The whole song is one horizontally scrolling strip: the spectrogram (roll.py's multi-resolution rows, as JPEG tiles)
with note outlines, a piano roll by part, chords and sections, bar or beat lines (as the PNG pages), drum hits, and
the audio (AUDIO.m4a, AAC, embedded) with a playhead the view follows. --marks adds roll.py's marks per page.
Everything is embedded: a CSP allows only inline script and style and data: images and media, the file has no
external URL, and every text from the result is in one JSON block with <, >, & and U+2028/9 escaped, put on the
page with textContent only. AUDIO.wav is 16-bit PCM (roll.py's input); the song is at most DURATION_MAX s.
Exit 0 with OUT written, 2 on bad arguments, 1 on any other failure (OUT is never left half-written; over SIZE_MAX
is a failure). Run by server.py / local_cli.py under limit_exec.py --view; never imported by them.
"""
import base64
import io
import json
import os
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # -I drops the script dir
import roll  # noqa: E402
import sheet  # noqa: E402

SIZE_MAX = 24 * 1024 * 1024  # bytes of HTML; a 600 s song with 96 kbps AAC is about 13 MiB
DURATION_MAX = 600.0  # s, as the server's audio cap
COL_S = 0.02  # s per spectrogram column
TILE_COLS = 1000  # columns per JPEG tile (20 s)
JPEG_QUALITY = 80
TEMPLATE = os.path.join(HERE, "view.html")
MARKS_NOTE = "marks: unvalidated heuristics, not accuracy"


def esc_json(obj):
    """JSON safe inside <script type="application/json">: no <, >, & or line separators in the text."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for a, b in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), (" ", "\\u2028"), (" ", "\\u2029")):
        s = s.replace(a, b)
    return s


def text(x, n=80):
    return str(x)[:n] if x is not None else ""


def build(v, wav, aac, with_marks):
    import numpy as np
    from matplotlib import colormaps
    from PIL import Image

    with wave.open(wav) as w:
        dur = w.getnframes() / w.getframerate()
    if not 0 < dur <= DURATION_MAX:
        raise ValueError("audio duration outside 0..%g s" % DURATION_MAX)
    pages = sheet.pages(v)
    sus, missed = {}, []
    if with_marks:
        for p in pages:
            an = roll.analyse(np, wav, p["start"], min(p["end"], dur))
            s_, m_ = roll.marks(np, v, an, p["start"], min(p["end"], dur))
            for s in s_:
                sus.setdefault((s["lane"], s["start"], s["midi"]), s)
            missed += [m for m in m_ if not any(o["midi"] == m["midi"] and o["start"] < m["end"] and o["end"] > m["start"]
                                                 for o in missed)]
    suspects = sorted(sus.values(), key=lambda s: (s["start"], s["midi"]))
    index = {(s["lane"], s["start"], s["midi"]): i for i, s in enumerate(suspects)}
    lanes = {}
    for key, color, lane in roll.LANES:
        notes = []
        for n in v.get(key) or []:
            if roll.MLO <= n["midi"] <= roll.MHI and n["end"] > n["start"]:
                notes.append({"s": round(n["start"], 3), "e": round(n["end"], 3), "m": int(n["midi"]),
                              "n": roll.name(int(n["midi"]))})
                if (lane, n["start"], n["midi"]) in index:
                    notes[-1]["f"] = index[(lane, n["start"], n["midi"])]  # its suspect entry
        lanes[lane] = {"color": color, "notes": notes}
    ms = [n["m"] for L in lanes.values() for n in L["notes"]] + [m["midi"] for m in missed]
    lo, hi = max(roll.MLO, (min(ms) if ms else 48) - 5), min(roll.MHI, (max(ms) if ms else 72) + 5)

    with wave.open(wav) as w:
        sr = w.getframerate()
    hop = max(1, int(round(COL_S * sr)))
    _, times, rows, M = roll.stft_rows(np, wav, 0.0, dur, hop=hop)
    db = 20 * np.log10(M + 1e-9)
    keep = (rows >= lo - 0.5) & (rows <= hi + 0.5)
    img = db[keep]
    top = float(img.max())
    idx = (np.clip((img - (top - roll.RANGE_DB)) / roll.RANGE_DB, 0, 1)[::-1] * 255).astype(np.uint8)  # high on top
    del img, db, M
    lut = (colormaps["magma"](np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)
    rgb = lut[idx]
    col = hop / sr
    tiles = []
    for j in range(0, rgb.shape[1], TILE_COLS):
        part = rgb[:, j:j + TILE_COLS]
        buf = io.BytesIO()
        Image.fromarray(part).save(buf, "JPEG", quality=JPEG_QUALITY)
        tiles.append({"t0": round(float(times[j]) - col / 2, 4), "t1": round(float(times[j]) + (part.shape[1] - 0.5) * col, 4),
                      "src": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")})

    lines, notes_seen = {}, []
    for p in pages:
        ls, note = roll.grid(v, p["start"], p["end"])
        for t, bar in ls:
            lines[round(t, 3)] = lines.get(round(t, 3), False) or bar
        if note not in notes_seen:
            notes_seen.append(note)
    chords = [{"s": round(c["start"], 3), "e": round(c["end"], 3), "l": text(c.get("display") or c.get("label"), 16)}
              for c in v.get("chords") or [] if c.get("end", 0) > c.get("start", 0)]
    sections = [{"s": round(x["start"], 3), "e": round(x["end"], 3), "l": text(x.get("label"), 24),
                 "f": text(x.get("function"), 24)} for x in v.get("sections") or [] if x.get("end", 0) > x.get("start", 0)]
    drums = [{"t": round(h["time"], 3), "c": text(h.get("class"), 12)} for h in v.get("drum_hits") or []]
    key = v.get("key") or {}
    with open(aac, "rb") as f:
        audio = f.read()
    data = {
        "duration": round(dur, 3), "lo": lo, "hi": hi, "tiles": tiles, "lanes": lanes,
        "lines": sorted([t, b] for t, b in lines.items()), "chords": chords, "sections": sections,
        "drums": drums, "drum_classes": list(roll.DRUMS), "drum_colors": list(roll.DRUM_COLORS),
        "header": "%s BPM   key %s %s" % (("%.0f" % v["bpm"]) if isinstance(v.get("bpm"), (int, float)) else "?",
                                          text(key.get("tonic"), 4), text(key.get("scale"), 12)),
        "notes": notes_seen + ([MARKS_NOTE] if with_marks else []),
        "marks": {"suspects": suspects, "missed": missed} if with_marks else None,
        "audio": "data:audio/mp4;base64," + base64.b64encode(audio).decode("ascii"),
    }
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    if tpl.count("@DATA@") != 1:
        raise ValueError("template")
    return tpl.replace("@DATA@", esc_json(data)).encode("utf-8")


def save(data, out):
    if len(data) > SIZE_MAX:
        raise ValueError("HTML over %d bytes" % SIZE_MAX)
    tmp = out + ".part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.link(tmp, out)  # fails when OUT exists: never overwrite
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def main(argv):
    args = argv[1:]
    marks = args[-1:] == ["--marks"]
    if marks:
        args = args[:-1]
    if len(args) != 4 or any(a.startswith("-") for a in args):
        sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
        return 2
    result, wav, aac, out = args
    if os.path.lexists(out):
        sys.stderr.write("output exists\n")
        return 2
    try:
        with open(result, encoding="utf-8") as f:
            v = json.load(f)
        save(build(v, wav, aac, marks), out)
    except Exception as e:  # any failure is exit 1 with no output file
        sys.stderr.write("view failed: %s: %s\n" % (type(e).__name__, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
