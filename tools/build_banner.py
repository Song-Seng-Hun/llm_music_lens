#!/usr/bin/env python3
"""Draw the animated pixel-art banner at the top of the READMEs.

    python3 tools/build_banner.py

writes assets/banner-dark.svg and assets/banner-light.svg: an analysis screen where a playhead
runs over a waveform, beat marks, a piano roll and chords, then reports the BPM and key; below it
a speaker plays, Claude listens on headphones and receives the report. The pixel helpers,
palettes, font and the Claude sprite come from the profile map (Song-Seng-Hun/Song-Seng-Hun
tools/build_worldmap.py), so the banners look alike. 160x90 grid, 4 px per cell, SMIL animation
(GitHub plays it in README images). Standard library only.
"""

import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

P = 4                 # px per grid cell
W, H = 160, 90        # grid size

# ---------------------------------------------------------------- palettes (from build_worldmap.py)

COMMON = {
    "skin": "#F1C9A5", "hair": "#3B2A22", "glass": "#1A1A2A",
    "coat": "#4A6FA5", "coat_d": "#38598A", "pants": "#2E3A55", "scarf": "#E8C468",
    "orange": "#D97757", "yellow": "#E3B341", "teal": "#4FA3A5",
    "green": "#6DBE73", "blue": "#5E8FD6", "red": "#E5566B",
    "white": "#F7F5EE", "paper": "#FFFDF6", "ink": "#4A5070",
    "flower1": "#E8A0B4", "flower2": "#E8D27A",
}

PALETTES = {
    "dark": {
        **COMMON,
        "K": "#0B0E1C", "frame": "#B89B5E", "frame_d": "#5E4C2C",
        "sky0": "#0C1026", "sky1": "#111735", "sky2": "#171F45", "sky3": "#1D2855", "sky4": "#253366",
        "star": "#F4EBD9", "star_d": "#7F88B5", "cloud": "#2A3566", "cloud_d": "#222C58",
        "mtn_far": "#1A2350", "g0": "#1F3B35", "g1": "#264840", "g2": "#19312C", "tuft": "#2F5A4D",
        "path": "#5C4B3F", "path_e": "#3F332B", "path_h": "#6E5A4B",
        "wall": "#8B8FA8", "wall_m": "#6C7192", "wall_d": "#4C5172",
        "roof": "#7B4A57", "roof_d": "#5A3442", "wood": "#6B4E3A", "wood_d": "#4A3527",
        "metal": "#9AA3B5", "metal_d": "#5F6880", "win": "#262E4D",
        "lit": "#FFD27A", "glow": ("#FFB85C", 0.32), "smoke": ("#9AA3B5", 0.7),
        "text": "#F4EBD9", "sub": "#8E96C2", "shadow": "#0B0E1C",
    },
    "light": {
        **COMMON,
        "K": "#2B2F45", "frame": "#C9A86A", "frame_d": "#8A6E3E",
        "sky0": "#B9D8EA", "sky1": "#C6E0EE", "sky2": "#D2E7F0", "sky3": "#DDEDF1", "sky4": "#E8F2EF",
        "star": "#FFFFFF", "star_d": "#FFFFFF", "cloud": "#FFFFFF", "cloud_d": "#E3ECF2",
        "mtn_far": "#A9C2D4", "g0": "#B4CE98", "g1": "#C1D8A6", "g2": "#A2BE87", "tuft": "#8CB072",
        "path": "#E2D2AE", "path_e": "#C9B48C", "path_h": "#EEE2C6",
        "wall": "#F2EEE6", "wall_m": "#DAD3C6", "wall_d": "#B9B0A1",
        "roof": "#C97E6C", "roof_d": "#A8655A", "wood": "#A47C5B", "wood_d": "#86624A",
        "metal": "#C6CDD6", "metal_d": "#8E97A6", "win": "#7F95AA",
        "lit": "#FFE08A", "glow": ("#FFC94A", 0.38), "smoke": ("#8E97A6", 0.55),
        "text": "#2B2F45", "sub": "#6A7192", "shadow": "#F7F3E8",
    },
}

# sprite characters -> palette keys
CHARS = {
    "K": "K", "h": "hair", "f": "skin", "G": "glass", "c": "coat", "C": "coat_d",
    "n": "pants", "s": "scarf", "o": "orange", "w": "white", "p": "paper", "i": "ink",
    "y": "yellow", "m": "metal", "M": "metal_d", "l": "lit", "r": "red", "g": "green",
    "W": "wood_d", "v": "wood", "R": "roof", "x": "text", "u": "sub",
}

# ---------------------------------------------------------------- canvas (from build_worldmap.py)


class Canvas:
    def __init__(self):
        self.px = {}

    def set(self, x, y, col):
        self.px[(x, y)] = col

    def rect(self, x, y, w, h, col):
        for j in range(y, y + h):
            for i in range(x, x + w):
                self.px[(i, j)] = col

    def box(self, x, y, w, h, fill, edge="K"):
        self.rect(x, y, w, h, edge)
        if w > 2 and h > 2:
            self.rect(x + 1, y + 1, w - 2, h - 2, fill)

    def sprite(self, rows, x, y):
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch != ".":
                    self.px[(x + i, y + j)] = CHARS[ch]

    def merge(self, other):
        self.px.update(other.px)


def outlined(c, col="K"):
    out = Canvas()
    for (x, y) in c.px:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (x + dx, y + dy) not in c.px:
                out.px[(x + dx, y + dy)] = col
    out.px.update(c.px)
    return out


def rects(cells):
    """Cover a set of cells with few rectangles (row runs merged downwards)."""
    rows = {}
    for x, y in cells:
        rows.setdefault(y, []).append(x)
    out, open_ = [], {}
    for y in sorted(rows):
        xs = sorted(rows[y])
        runs, start, prev = [], xs[0], xs[0]
        for x in xs[1:]:
            if x != prev + 1:
                runs.append((start, prev - start + 1))
                start = x
            prev = x
        runs.append((start, prev - start + 1))
        nxt = {}
        for key in runs:
            if key in open_ and sum(open_[key]) == y:
                y0, h = open_.pop(key)
                nxt[key] = (y0, h + 1)
            else:
                nxt[key] = (y, 1)
        out += [(k[0], y0, k[1], h) for k, (y0, h) in open_.items()]
        open_ = nxt
    out += [(k[0], y0, k[1], h) for k, (y0, h) in open_.items()]
    return out


def svg_paths(c, pal, unit=P, ox=0, oy=0):
    by_col = {}
    for xy, col in c.px.items():
        by_col.setdefault(col, set()).add(xy)
    out = []
    for col in sorted(by_col):
        d = "".join(f"M{x*unit+ox} {y*unit+oy}h{w*unit}v{h*unit}h{-w*unit}z"
                    for x, y, w, h in sorted(rects(by_col[col]), key=lambda r: (r[1], r[0])))
        fill = pal[col]
        if isinstance(fill, tuple):
            out.append(f'<path fill="{fill[0]}" fill-opacity="{fill[1]}" d="{d}"/>')
        else:
            out.append(f'<path fill="{fill}" d="{d}"/>')
    return "".join(out)

# ---------------------------------------------------------------- animation helpers (from build_worldmap.py)

T = 12.0              # loop length in seconds
DT = 1 / 16           # timeline step in seconds
TICKS = [k * DT for k in range(int(T / DT))]
FADE = T - 1.0        # lights go out and everything resets from here


def _kt(t):
    return f"{t / T:.4f}".rstrip("0").rstrip(".")


def _steps(changes):
    ts, vs = [], []
    for t, v in changes:
        if vs and vs[-1] == v:
            continue
        if ts and abs(t - ts[-1]) < 1e-9:
            vs[-1] = v
            continue
        ts.append(t)
        vs.append(v)
    ts[0] = 0.0
    return ";".join(_kt(t) or "0" for t in ts), ";".join(str(v) for v in vs)


def timeline(attr, changes):
    """Discrete animation on the shared T-second loop."""
    k, v = _steps(changes)
    if attr == "translate":
        return (f'<animateTransform attributeName="transform" type="translate" dur="{T}s" '
                f'repeatCount="indefinite" calcMode="discrete" keyTimes="{k}" values="{v}"/>')
    return (f'<animate attributeName="{attr}" dur="{T}s" repeatCount="indefinite" '
            f'calcMode="discrete" keyTimes="{k}" values="{v}"/>')


def loop(attr, dur, values, keytimes=None, begin=0.0, discrete=True, translate=False):
    """Free-running ambient animation with its own period."""
    kt = f' keyTimes="{keytimes}"' if keytimes else ""
    mode = ' calcMode="discrete"' if discrete else ""
    beg = f' begin="{begin:.2f}s"' if begin else ""
    if translate:
        return (f'<animateTransform attributeName="transform" type="translate" dur="{dur}s"'
                f'{beg} repeatCount="indefinite"{mode}{kt} values="{values}"/>')
    return f'<animate attributeName="{attr}" dur="{dur}s"{beg} repeatCount="indefinite"{mode}{kt} values="{values}"/>'


def group(inner, anim="", opacity=None, transform=None):
    attrs = ""
    if opacity is not None:
        attrs += f' opacity="{opacity}"'
    if transform:
        attrs += f' transform="{transform}"'
    return f"<g{attrs}>{inner}{anim}</g>"


def shown(inner, t_on, t_off):
    """Visible from t_on to t_off on the loop."""
    return group(inner, timeline("opacity", [(0, 0), (t_on, 1), (t_off, 0)]), opacity=0)


def sparkle(pal, x, y, t_on):
    small, big = Canvas(), Canvas()
    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
        small.set(x + dx, y + dy, "lit")
    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (-2, 0), (0, 2), (0, -2)):
        big.set(x + dx, y + dy, "lit" if abs(dx) + abs(dy) < 2 else "white")
    return (group(svg_paths(small, pal), timeline("opacity", [(0, 0), (t_on, 1), (t_on + 0.2, 0),
                                                            (t_on + 0.45, 1), (t_on + 0.65, 0)]), opacity=0)
            + group(svg_paths(big, pal), timeline("opacity", [(0, 0), (t_on + 0.2, 1), (t_on + 0.45, 0)]),
                    opacity=0))

# ---------------------------------------------------------------- font 5x7 (from build_worldmap.py)


FONT_SRC = """
A .###. #...# #...# ##### #...# #...# #...#
B ####. #...# #...# ####. #...# #...# ####.
C .###. #...# #.... #.... #.... #...# .###.
D ####. #...# #...# #...# #...# #...# ####.
E ##### #.... #.... ####. #.... #.... #####
F ##### #.... #.... ####. #.... #.... #....
G .###. #...# #.... #.### #...# #...# .####
H #...# #...# #...# ##### #...# #...# #...#
I ### .#. .#. .#. .#. .#. ###
J ..### ...#. ...#. ...#. ...#. #..#. .##..
K #...# #..#. #.#.. ##... #.#.. #..#. #...#
L #.... #.... #.... #.... #.... #.... #####
M #...# ##.## #.#.# #.#.# #...# #...# #...#
N #...# #...# ##..# #.#.# #..## #...# #...#
O .###. #...# #...# #...# #...# #...# .###.
P ####. #...# #...# ####. #.... #.... #....
Q .###. #...# #...# #...# #.#.# #..#. .##.#
R ####. #...# #...# ####. #.#.. #..#. #...#
S .#### #.... #.... .###. ....# ....# ####.
T ##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..
U #...# #...# #...# #...# #...# #...# .###.
V #...# #...# #...# #...# #...# .#.#. ..#..
W #...# #...# #...# #.#.# #.#.# #.#.# .#.#.
X #...# #...# .#.#. ..#.. .#.#. #...# #...#
Y #...# #...# .#.#. ..#.. ..#.. ..#.. ..#..
Z ##### ....# ...#. ..#.. .#... #.... #####
0 .###. #...# #..## #.#.# ##..# #...# .###.
1 .#. ##. .#. .#. .#. .#. ###
2 .###. #...# ....# ...#. ..#.. .#... #####
3 ####. ....# ....# .###. ....# ....# ####.
4 ...#. ..##. .#.#. #..#. ##### ...#. ...#.
5 ##### #.... ####. ....# ....# #...# .###.
6 .###. #.... #.... ####. #...# #...# .###.
. . . . . . . #
· . . . # . . .
- ... ... ... ### ... ... ...
m ..... ..... ##.#. #.#.# #.#.# #.#.# #.#.#
"""

FONT = {" ": ["..."] * 7}
for line in FONT_SRC.strip().splitlines():
    ch, *rows = line.split()
    FONT[ch] = rows


def text_width(s):
    return sum(len(FONT[ch][0]) + 1 for ch in s) - 1


def draw_text(c, s, x, y, col, shadow="shadow"):
    for pass_col, dy in ((shadow, 1), (col, 0)):
        if pass_col is None:
            continue
        cx = x
        for ch in s:
            for j, row in enumerate(FONT[ch]):
                for i, v in enumerate(row):
                    if v == "#":
                        c.set(cx + i, y + j + dy, pass_col)
            cx += len(FONT[ch][0]) + 1
    return x + text_width(s)

# ---------------------------------------------------------------- sprites (traveler and Claude from build_worldmap.py)


# Claude: body = cols 1-11 x rows 0-7 from the body's top-left, arms beside it, legs on rows 8-9.
CLAUDE_EYES = {
    "happy": [(2, 2), (3, 3), (2, 4), (10, 2), (9, 3), (10, 4)],   # > <
    "dot": [(3, 2), (3, 3), (9, 2), (9, 3)],
    "blink": [(2, 3), (3, 3), (9, 3), (10, 3)],
    "right": [(4, 2), (4, 3), (10, 2), (10, 3)],
}
_ARM_L, _ARM_R = [(0, 3), (0, 4), (0, 5)], [(12, 3), (12, 4), (12, 5)]
_UP_L, _UP_R = [(0, -2), (0, -1), (0, 0)], [(12, -2), (12, -1), (12, 0)]
CLAUDE_ARMS = {
    "down": _ARM_L + _ARM_R,
    "up": _UP_L + _UP_R,
    "wave1": _UP_L + _ARM_R,
    "wave2": [(0, 1), (-1, 0), (-1, -1)] + _ARM_R,
}
CLAUDE_LEGS = {
    "stand": [(x, y) for x in (2, 4, 8, 10) for y in (8, 9)],
    "stepA": [(2, 8), (2, 9), (8, 8), (8, 9), (4, 8), (10, 8)],
    "stepB": [(4, 8), (4, 9), (10, 8), (10, 9), (2, 8), (8, 8)],
}


def claude_frame(eyes, arms, legs, bob):
    c = Canvas()
    ox, oy = 1, 3 - bob          # two spare rows on top for raised arms
    c.rect(ox + 1, oy, 11, 8, "orange")
    for x, y in CLAUDE_ARMS[arms] + CLAUDE_LEGS[legs]:
        c.set(ox + x, oy + y, "orange")
    for x, y in CLAUDE_EYES[eyes]:
        c.set(ox + x, oy + y, "K")
    return outlined(c)


# ---------------------------------------------------------------- scene

THEME = {
    "dark": {"screen": "#101528", "wave_dim": "#2A3566", "wave": "#6CC4E8", "note_dim": "#2E3A5E",
             "note": "#E8A0B4", "bass": "#B39CF0", "grid": "#1E2747", "speaker": "#3B3F55",
             "cone": "#1A1D2B"},
    "light": {"screen": "#FFFDF6", "wave_dim": "#D3DCE8", "wave": "#3E8FC4", "note_dim": "#E4E2EE",
              "note": "#D9667F", "bass": "#7D6BD6", "grid": "#EEF1F6", "speaker": "#5A5F78",
              "cone": "#2B2F45"},
}
ENVELOPE = ["ppppppp", "pipppip", "ppipipp", "pppippp", "ppppppp"]
NOTE = ["..ll", "..l.", "..l.", "lll.", "lll."]           # a floating music note (outlined)

SCREEN = (8, 11, 144, 43)                 # x, y, w, h of the analysis screen
X0, BEAT_W, BEATS = 14, 8, 16              # first beat column, cells per beat, beats shown
WAVE_Y = 24                                # waveform centre row
TICK_Y = 32                                # beat marks, between the waveform and the roll
ROLL_Y = 33                                # top row of the piano roll
CHORD_Y = 49                               # chord label row
FLOOR = 81
CLAUDE_AT = (100, FLOOR - 1)
SPEAKER = (16, 62)                         # top-left of the speaker
CHORDS = ["C", "Am", "F", "G"]
# melody (start beat, beats, row from the top of the roll) and one bass note per bar
MELODY = [(0, 1, 8), (1, 1, 6), (2, 2, 4), (4, 1, 7), (5, 1, 9), (6, 2, 6), (8, 1, 5), (9, 1, 4),
          (10, 2, 3), (12, 1, 6), (13, 1, 5), (14, 2, 7)]
BASS = [(0, 4, 12), (4, 4, 13), (8, 4, 11), (12, 4, 10)]


def beat_x(k):
    return X0 + k * BEAT_W


def amplitude(x):
    """Rows above and below the centre: a hit on every beat, harder on the first of each bar."""
    k, into = divmod(x - X0, BEAT_W)
    hit = 4.5 if k % 4 == 0 else 3.0
    return max(1, round(1.2 + hit * math.exp(-into / 2.6) + 0.8 * math.sin(x * 1.7)))


def draw_room(c):
    bands = ["sky0", "sky1", "sky2", "sky3", "sky4"]
    for y in range(2, FLOOR):
        k = min(4, (y - 2) * 5 // (FLOOR - 2))
        for x in range(2, W - 2):
            c.set(x, y, bands[k])
    for y in range(FLOOR, H - 2):
        for x in range(2, W - 2):
            c.set(x, y, "wood_d" if (y - FLOOR) % 3 == 0 or (x + 7 * ((y - FLOOR) // 3)) % 23 == 0 else "wood")


def draw_screen(c):
    x, y, w, h = SCREEN
    c.box(x, y, w, h, "screen")
    for k in range(BEATS + 1):                    # faint beat grid behind everything
        for yy in range(y + 7, y + h - 1):
            c.set(beat_x(k), yy, "grid")
    c.rect(x + 1, y + h - 7, w - 2, 1, "grid")
    c.box(x + w // 2 - 6, y + h, 13, 3, "metal_d")             # stand
    c.rect(x + w // 2 - 10, y + h + 3, 21, 1, "metal_d")


def wave_canvas(col):
    c = Canvas()
    for x in range(X0, beat_x(BEATS)):
        a = amplitude(x)
        c.rect(x, WAVE_Y - a, 1, 2 * a + 1, col)
    return c


def roll_canvas(note_col, bass_col):
    c = Canvas()
    for start, length, row in MELODY:
        c.rect(beat_x(start) + 1, ROLL_Y + row, length * BEAT_W - 2, 1, note_col)
    for start, length, row in BASS:
        c.rect(beat_x(start) + 1, ROLL_Y + row, length * BEAT_W - 2, 1, bass_col)
    return c


def draw_speaker(c):
    x, y = SPEAKER
    c.box(x, y, 16, FLOOR - y, "speaker")
    for cy, r in ((y + 7, 4), (y + 15, 2)):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    c.set(x + 8 + dx, cy + dy, "cone")


def claude_dj(eyes, arms, bob):
    """Claude (as in build_worldmap.py) wearing headphones."""
    c = Canvas()
    ox, oy = 1, 3 - bob
    c.rect(ox + 1, oy, 11, 8, "orange")
    for px, py in CLAUDE_ARMS[arms] + CLAUDE_LEGS["stand"]:
        c.set(ox + px, oy + py, "orange")
    for px, py in CLAUDE_EYES[eyes]:
        c.set(ox + px, oy + py, "K")
    c.rect(ox + 2, oy - 2, 9, 1, "metal_d")                   # headband
    c.set(ox + 1, oy - 1, "metal_d")
    c.set(ox + 11, oy - 1, "metal_d")
    for cx in (ox - 1, ox + 12):                              # ear cups
        c.rect(cx, oy + 1, 2, 3, "ink")
    return outlined(c)

# ---------------------------------------------------------------- story
# 120 BPM: the playhead crosses four bars in 8 s. Each beat flashes its tick and the speaker,
# Claude nods, notes light up as they are passed, each bar shows its chord; then the BPM and the
# key are reported and the report flies to Claude.

BEAT = 0.5
START = 0.5
END = START + BEATS * BEAT
REPORT = END + 0.25                       # the key is shown and the report leaves the screen
CATCH = REPORT + 0.75


def snap(x, y):
    return f"{round(x) * P} {round(y) * P}"


def playhead_x(t):
    u = min(1.0, max(0.0, (t - START) / (END - START)))
    return X0 + u * BEATS * BEAT_W


def played_layer(pal):
    """Bright waveform and notes, revealed up to the playhead by an animated clip."""
    x, y, w, h = SCREEN
    width = [(t, round(playhead_x(t) - X0) * P if t < FADE else 0) for t in TICKS]
    clip = (f'<clipPath id="played"><rect x="{X0 * P}" y="{y * P}" width="0" height="{h * P}">'
            f'{timeline("width", width)}</rect></clipPath>')
    bright = svg_paths(wave_canvas("wave"), pal) + svg_paths(roll_canvas("note", "bass"), pal)
    head = Canvas()
    head.rect(0, y + 7, 1, h - 9, "red")
    pos = [(t, snap(playhead_x(t), 0)) for t in TICKS]
    vis = [(0, 0), (START, 1), (END + 0.25, 0)]
    return (f'<defs>{clip}</defs><g clip-path="url(#played)">{bright}</g>'
            + group(group(svg_paths(head, pal), timeline("opacity", vis), opacity=0), timeline("translate", pos)))


def beat_layer(pal):
    out = ""
    for k in range(BEATS):
        t = START + k * BEAT
        tick = Canvas()
        tick.rect(beat_x(k), TICK_Y, 1, 1 if k % 4 else 2, "lit")
        out += group(svg_paths(tick, pal), timeline("opacity", [(0, 0), (t, 1), (t + 0.19, 0.45), (FADE, 0)]),
                     opacity=0)
        pulse = Canvas()                                      # the speaker cone pushes out on the beat
        x, y = SPEAKER
        for dx, dy in ((-5, 0), (5, 0), (0, -5), (0, 5)):
            pulse.set(x + 8 + dx, y + 7 + dy, "lit")
        out += group(svg_paths(pulse, pal), timeline("opacity", [(0, 0), (t, 1), (t + 0.19, 0)]), opacity=0)
    for b, name in enumerate(CHORDS):                         # chord labels, unit 2 px
        lab = Canvas()
        draw_text(lab, name, (beat_x(4 * b) + 2) * 2, CHORD_Y * 2 + 1, "text", shadow=None)
        out += shown(svg_paths(lab, pal, unit=2), START + 4 * b * BEAT, FADE)
    x, y, w, h = SCREEN
    bpm, key = Canvas(), Canvas()
    draw_text(bpm, "BPM 120", (x + 3) * 2, (y + 2) * 2, "text", shadow=None)
    draw_text(key, "KEY C MAJOR", (x + w - 3) * 2 - text_width("KEY C MAJOR"), (y + 2) * 2, "text", shadow=None)
    out += shown(svg_paths(bpm, pal, unit=2), START + 4 * BEAT, FADE)
    out += shown(svg_paths(key, pal, unit=2), REPORT, FADE)
    return out


def float_notes(pal):
    """A note drifts from the speaker towards Claude every other beat."""
    out = ""
    for k in range(0, BEATS, 2):
        t0 = START + k * BEAT
        y = SPEAKER[1] + 2 + (k // 2 % 3) * 4
        pos, vis = [], []
        for t in TICKS:
            u = (t - t0) / 1.6
            pos.append((t, snap(SPEAKER[0] + 17 + 44 * min(max(u, 0), 1), y + 2 * math.sin(7 * u))))
            vis.append((t, 1 if 0 <= u < 1 else 0))
        out += group(group(svg_paths(outlined_sprite(NOTE), pal), timeline("opacity", vis), opacity=0),
                     timeline("translate", pos))
    return out


def outlined_sprite(rows):
    c = Canvas()
    c.sprite(rows, 0, 0)
    return outlined(c)


def report_layer(pal):
    """The analysis report flies from the screen down into Claude's hands."""
    x, y, w, h = SCREEN
    a, b = (x + w - 30, y + h - 4), (CLAUDE_AT[0] - 3, CLAUDE_AT[1] - 19)
    pos, vis = [], []
    for t in TICKS:
        u = min(1.0, max(0.0, (t - REPORT) / (CATCH - REPORT)))
        px, py = a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u - 6 * math.sin(math.pi * u)
        pos.append((t, snap(px, py)))
        vis.append((t, 1 if REPORT <= t < FADE else 0))
    return group(group(svg_paths(outlined_sprite(ENVELOPE), pal), timeline("opacity", vis), opacity=0),
                 timeline("translate", pos))


def claude_pose(t, k):
    if CATCH <= t < FADE:
        return ("happy", "up", 0)
    if START <= t < END:
        into = ((t - START) % BEAT) / BEAT
        return ("happy", "down", 1 if into < 0.5 else 0)        # nods on every beat
    blink = any(a <= t < a + 0.125 for a in (0.2, FADE + 0.4))
    return ("blink" if blink else "dot", "down", 0)


def claude_layer(pal):
    seq = [(t, claude_pose(t, k)) for k, t in enumerate(TICKS)]
    frames = {pose: claude_dj(*pose) for pose in dict.fromkeys(p for _, p in seq)}
    ox, oy = (CLAUDE_AT[0] - 7) * P, (CLAUDE_AT[1] - 13) * P
    return "".join(group(svg_paths(fc, pal, ox=ox, oy=oy),
                         timeline("opacity", [(t, 1 if p == pose else 0) for t, p in seq]),
                         opacity=1 if pose == seq[0][1] else 0)
                   for pose, fc in frames.items())


def frame_border():
    f = Canvas()
    f.rect(0, 0, W, H, "frame_d")
    f.rect(1, 1, W - 2, H - 2, "frame")
    for y in range(2, H - 2):
        for x in range(2, W - 2):
            f.px.pop((x, y), None)
    for x, y in ((0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)):
        f.px.pop((x, y), None)
    for x, y in ((1, 1), (W - 2, 1), (1, H - 2), (W - 2, H - 2)):
        f.set(x, y, "frame_d")
    return f


def build(theme):
    pal = {**PALETTES[theme], **THEME[theme]}
    bg = Canvas()
    draw_room(bg)
    draw_screen(bg)
    bg.merge(wave_canvas("wave_dim"))
    bg.merge(roll_canvas("note_dim", "note_dim"))
    draw_speaker(bg)
    title = Canvas()                                          # unit 3 px
    draw_text(title, "LLM MUSIC LENS", W * P // 6 - text_width("LLM MUSIC LENS") // 2, 4, "text")
    desc = ("Pixel-art banner: a playhead runs over a waveform, beat marks and a piano roll at 120 BPM, "
            "lighting each beat and note and showing the chords C, Am, F and G; the screen then reports "
            "BPM 120 and the key C major, and the report flies to Claude, who nods along in headphones "
            "next to a speaker.")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W * P} {H * P}" width="{W * P}" '
        f'height="{H * P}" shape-rendering="crispEdges" role="img" aria-labelledby="t d">'
        f'<title id="t">LLM Music Lens</title><desc id="d">{desc}</desc>'
        + svg_paths(bg, pal) + played_layer(pal) + beat_layer(pal) + float_notes(pal)
        + claude_layer(pal) + report_layer(pal)
        + svg_paths(title, pal, unit=3) + svg_paths(frame_border(), pal)
        + "</svg>\n"
    )


def main():
    OUT.mkdir(exist_ok=True)
    for theme in PALETTES:
        path = OUT / f"banner-{theme}.svg"
        path.write_text(build(theme), encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
