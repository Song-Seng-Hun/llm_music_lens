"""LLM sheet (music-lens-sheet/1): a compact, section-indexed reading of one stored rhythm result, built only from
that result and rendered as English text from the JSON alone. Served by server.py at GET /sheet/<result_id>.

Every source section is kept with an ID (S01...) and its source times. Layers are joined to sections by exact interval;
bars belong to the section holding their midpoint, with straddling bars and uncovered spans listed. Each layer carries a
status per section: detected, empty (measured, nothing found), unavailable (not measured or no row) or failed.
Repeated chord rows point at the first section that had them; drum grooves are song-wide IDs with coverage counts;
part changes are compared only with the previous measured section; no-chord intervals stay as N.C.
On 45 frozen questions over four stored results (workers 0.4.3-0.21.0) an LLM reading this sheet answered more
correctly than one reading the /report JSON, with no claims about unmeasured layers (see docs/sheet-eval.md).
GET /sheet/<result_id>?start=&end= serves zoom() instead: a bar-by-bar reading of up to ZOOM_MAX seconds.
CLI: python3 -I sheet.py RESULT.json OUT.json OUT.txt [START END]"""
import bisect, collections, itertools, json, os, re, sys

LBL = {'reference_candidate': 'cand', 'low_reference': 'low'}
NAMES = 'C C# D D# E F F# G G# A A# B'.split()
PARTS = ('vocals', 'bass', 'drums')
LAYERS = 'beats|chords|energy|melody|genre|bass|other|drums|bars'
EPS = 0.02      # interval join tolerance, s (source times have 2 decimals)
STRADDLE = 0.1  # a bar straddles when more than this share of it lies outside its section
GAP = 1.0       # uncovered spans shorter than this, s, are not listed
KEY_MIN = 2.0   # key segments overlapping a section by less than this, s, are not listed as changes
ZOOM_MAX = 30.0  # s; the same span as one POST /earcheck clip
STEPS = 16      # steps per bar when the result gives none (worker before 0.22.0)
NOTE_LAYERS = (('melody', 'melody_notes', 'melody'), ('bass', 'bass_notes', 'bass'), ('other', 'other_notes', 'other'))


def rel(x):
    return LBL.get(x.get('reliability') if isinstance(x, dict) else x, 'unknown')


def keyname(k):
    return k['tonic'] + ('m' if k['scale'] == 'minor' else '')


def note(m):
    return NAMES[m % 12] + str(m // 12 - 1)


def layer_state(v, k):
    """(status, reliability) of a dict layer: done | skipped | failed | absent."""
    x = v.get(k)
    if not isinstance(x, dict):
        return 'absent', 'unknown'
    return x.get('status', 'done'), rel(x)


def done(v, k):
    x = v.get(k)
    return x if isinstance(x, dict) and x.get('status', 'done') == 'done' else None


def meter(v):
    """(beats per bar or None, steps per bar) of the bar grid; results before worker 0.22.0 give (None, 16)."""
    b = done(v, 'bars') or {}
    return b.get('beats_per_bar'), b.get('steps') or STEPS


def melody_source(m):
    meth = (m or {}).get('method') or ''
    if 'YourMT3' in meth:
        return 'sung notes of the vocal stem; may include backing vocals'
    if not meth:
        return 'unknown method'
    s = meth.split('. ')[0]
    if len(s) > 90:  # cut at a word boundary, never mid-word
        s = s[:91].rsplit(' ', 1)[0].rstrip(',;:') + ' ...'
    return 'melody estimate: ' + s


def same_span(r, a, b):
    return abs(r['start'] - a) <= EPS and abs(r['end'] - b) <= EPS


def uncovered(a, b, spans):
    out, t = [], a
    for s, e in sorted(spans):
        if s > t and min(s, b) - t >= GAP:
            out.append([t, min(s, b)])
        t = max(t, e)
    if b - t >= GAP:
        out.append([t, b])
    return out


def timed(chords, a, b):
    """[[start, chord], ...] of chord changes overlapping [a, b); no-chord rows kept as N.C."""
    out = []
    for c in chords:
        if c['start'] < b and c['end'] > a:
            lab = 'N.C.' if c['label'] == 'N' else c.get('display', c['label'])
            if not out or out[-1][1] != lab:
                out.append([round(max(c['start'], a), 2), lab])
    return out


def drums_of(bs, idx, grooves):
    """Coverage first, then the most common non-empty pattern as a song-wide groove ID."""
    meas = [(i, r) for i, r in zip(idx, bs) if r.get('kick') is not None and r.get('snare') is not None]
    if not bs:
        return {'status': 'unavailable'}
    if not meas:
        return {'status': 'unavailable', 'bars': len(bs)}
    empty = [i + 1 for i, r in meas if not r['kick'] and not r['snare']]
    pats = collections.Counter((tuple(r['kick']), tuple(r['snare'])) for i, r in meas if r['kick'] or r['snare'])
    out = {'status': 'detected' if pats else 'empty', 'bars': len(bs), 'measured': len(meas), 'empty_bars': len(empty)}
    if pats:
        p, n = pats.most_common(1)[0]
        gid = grooves.setdefault(p, 'G%d' % (len(grooves) + 1))
        out.update(groove=gid, groove_bars=n, other_patterns=len(pats) - 1)
    return out


def build(v, rid=None):
    secs = v.get('sections') or []
    bars = (done(v, 'bars') or {}).get('rows')
    es = v.get('energy_summary') or {}
    med = es.get('median')
    st = {k: layer_state(v, k) for k in ('melody', 'parts', 'bars', 'drums', 'genre', 'swing')}
    mels = (done(v, 'melody') or {}).get('sections') or []
    pts = (done(v, 'parts') or {}).get('sections') or []
    chords = v.get('chords')
    span, out_secs, grooves, seen_chords = collections.Counter(), [], {}, {}
    prev = None  # previous section's measured active parts (None when it had no parts row)
    for n, s in enumerate(secs, 1):
        a, b = s['start'], s['end']
        sid = 'S%02d' % n
        ks = [k for k in (v.get('key_sections') or []) if k['start'] < b and k['end'] > a]
        key = keyname(max(ks, key=lambda k: min(k['end'], b) - max(k['start'], a))) if ks else None
        span[key] += b - a
        kch = [[max(k['start'], a), keyname(k)] for k in sorted(ks, key=lambda k: k['start']) if min(k['end'], b) - max(k['start'], a) >= KEY_MIN]
        sec = {'id': sid, 'label': s['label'], 'function': s.get('function'), 'start': a, 'end': b, 'key_estimate': key,
               'key_changes': kch if len({k for _, k in kch}) > 1 else [],
               'level_db': None if med is None or s.get('energy_mean') is None else round(s['energy_mean'] - med, 1)}
        # bars: a bar belongs to the section holding its midpoint
        idx = [i for i, r in enumerate(bars or []) if a <= (r['start'] + r['end']) / 2 < b]
        bs = [bars[i] for i in idx]
        if bars is not None:
            sec['bars'] = [idx[0] + 1, idx[-1] + 1] if idx else None
            sec['straddling_bars'] = [i + 1 for i, r in zip(idx, bs)
                                      if (max(0, a - r['start']) + max(0, r['end'] - b)) > STRADDLE * (r['end'] - r['start'])]
            sec['uncovered'] = uncovered(a, b, [(r['start'], r['end']) for r in bars])
        # chords: per bar when a bar grid with chords exists, else timed changes with N kept
        if bs and any(r.get('chord') is not None for r in bars):  # sections holding no bar fall back to timed changes
            ch = [r['split'] if r.get('split') else r['chord'] for r in bs]
            sec['chords'] = {'status': 'detected', 'per_bar': ch}
            if chords and sec.get('uncovered'):
                sec['chords']['outside_bars'] = [{'span': u, 'changes': timed(chords, u[0], u[1])} for u in sec['uncovered']]
            key_ch = json.dumps(ch)
            if key_ch in seen_chords:
                sec['chords']['same_as'] = seen_chords[key_ch]
            else:
                seen_chords[key_ch] = sid
        elif chords:
            cs = timed(chords, a, b)
            sec['chords'] = {'status': 'detected' if cs else 'empty', 'changes': cs}
        else:
            sec['chords'] = {'status': 'unavailable'}
        # melody
        if st['melody'][0] != 'done':
            sec['melody'] = {'status': 'failed' if st['melody'][0] == 'failed' else 'unavailable'}
        else:
            m = next((m for m in mels if same_span(m, a, b)), None)
            if m is None:
                sec['melody'] = {'status': 'unavailable'}
            elif not m['note_count']:
                sec['melody'] = {'status': 'empty'}
            else:
                sec['melody'] = {'status': 'detected', 'notes': m['note_count'], 'low': note(m['low']), 'high': note(m['high']),
                                 'peak': m['peak']['note'], 'peak_time': m['peak']['time'], 'direction': m['direction'],
                                 'notes_per_note_s': round(m['notes_per_second'], 1)}
        # parts: changes only against the adjacent measured section
        p = next((p for p in pts if same_span(p, a, b)), None) if st['parts'][0] == 'done' else None
        if p is None:
            sec['parts'] = {'status': 'failed' if st['parts'][0] == 'failed' else 'unavailable'}
            prev = None
        else:
            act = {k for k in PARTS if (p.get(k) or 0) >= 0.5}
            sec['parts'] = {'status': 'detected', **{k: None if p.get(k) is None else round(p[k], 2) for k in PARTS},
                            'other_notes_per_s': None if p.get('other_notes_per_s') is None else round(p['other_notes_per_s'], 1)}
            if prev is not None:
                sec['parts']['crossed_50_up'] = sorted(act - prev)
                sec['parts']['crossed_50_down'] = sorted(prev - act)
            prev = act
        # drums
        if st['drums'][0] in ('failed', 'skipped', 'absent') and bars is not None:
            sec['drums'] = {'status': 'failed' if st['drums'][0] == 'failed' else 'unavailable'}
        else:
            sec['drums'] = drums_of(bs, idx, grooves) if bars is not None else {'status': 'unavailable'}
        out_secs.append(sec)
    total = sum(span.values()) or 1
    tool, sw, k0 = v.get('tool') or {}, done(v, 'swing') or {}, v.get('key')
    lay = lambda k: {'status': st[k][0], 'reliability': st[k][1]}
    return {
        'format': 'music-lens-sheet/1', 'result_id': rid, 'worker': tool.get('version'), 'duration': v['duration'],
        'conventions': {
            'reliability': 'cand = worker reliability reference_candidate, low = low_reference; neither is a measured accuracy',
            'status': 'detected = measured and found; empty = measured, nothing found; unavailable = not measured or no row for this span; failed = the layer failed',
            'times': 'seconds from audio start, as in the source result',
            'key_estimate': 'the key_sections estimate with the longest overlap with the section',
            'level_db': 'section mean energy minus the song median, dB',
            'bars': 'a bar belongs to the section that holds its midpoint; straddling = more than %d%% of the bar lies outside; uncovered = spans of %.0f s or more with no bar' % (100 * STRADDLE, GAP),
            'parts': 'share of 2 s windows where the stem is within 15 dB of the mix; crossed_50 compares with the previous section only when both were measured',
            'notes_per_note_s': 'notes divided by the summed duration of those notes (sounding time, not section length)',
            'drums': 'measured = bars with kick and snare data; groove = most common non-empty kick/snare pattern, steps 0-%d (4 per beat)' % (meter(v)[1] - 1),
            'meter': 'beats per bar = median count of the section model\'s beats in a bar, given only when 3 or 4; unknown keeps 16 steps'},
        'song': {'tempo': {'bpm': v.get('bpm'), 'confidence': v.get('bpm_confidence'), 'confidence_max': 5.3, 'calibrated': False},
                 'meter': dict(zip(('beats_per_bar', 'steps'), meter(v))),
                 'feel': sw.get('feel'), 'downbeats': rel(v.get('downbeats') or {}),
                 'key': {'whole_song': keyname(k0) if k0 else None, 'by_section': [{'key': k, 'share': round(t / total, 2)} for k, t in span.most_common()]},
                 'form': [{'label': l, 'count': len(list(g))} for l, g in itertools.groupby(s['label'] for s in secs)],
                 'functions': {s['label']: s['function'] for s in reversed(secs) if s.get('function')},
                 'loudest': [es['peaks'][0]['start'], es['peaks'][0]['end']] if es.get('peaks') else None,
                 'quietest': [es['quietest']['start'], es['quietest']['end']] if es.get('quietest') else None,
                 'genre_guess': {'reliability': 'low', 'top': [t['style'].split('---')[-1] for t in (done(v, 'genre') or {}).get('top', [])[:3]]},
                 'layers': {'chords': {'status': 'done' if chords else 'absent', 'reliability': rel(v.get('chords_reliability')) if chords else 'unknown'},
                            'melody': lay('melody'), 'parts': lay('parts'), 'bars': lay('bars'), 'drums': lay('drums')},
                 'grooves': {g: {'kick': list(p[0]), 'snare': list(p[1])} for p, g in grooves.items()},
                 'melody_source': melody_source(done(v, 'melody'))},
        'sections': out_secs,
        'drill_down': {'request': 'GET /timeline/%s?layer=<layer>&start=<s>&end=<s>&limit=<1-200>&cursor=<n>' % (rid or '<result_id>'),
                       'layers': LAYERS, 'range': '0-600 s; rows overlapping [start, end]', 'paging': 'follow continuation as the next cursor',
                       'zoom': 'GET /sheet/%s?start=<s>&end=<s> for a bar-by-bar reading of up to %.0f s' % (rid or '<result_id>', ZOOM_MAX)}}


def render(sh):
    mm = lambda t: '?' if t is None else '%d:%05.2f' % (t // 60, t % 60)
    num = lambda x, f: '?' if x is None else f % x
    rng = lambda r: '%s-%s' % (mm(r[0]), mm(r[1])) if r else 'unknown'
    g, c = sh['song'], sh['conventions']
    grid = lambda steps, ch: ''.join(ch if i in steps else '.' for i in range(g['meter']['steps']))
    L = g['layers']
    out = ['MUSIC LENS SHEET (%s) | result %s | %s | worker %s' % (sh['format'], (sh['result_id'] or 'unknown')[:12], mm(sh['duration']), sh['worker']),
           'labels: %s.' % c['reliability'],
           'status: ' + c['status'] + '.',
           'tempo   %s BPM (confidence %s of ~%.1f, uncalibrated); feel %s; downbeats %s; beats per bar %s' % (num(g['tempo']['bpm'], '%.1f'), num(g['tempo']['confidence'], '%.1f'), g['tempo']['confidence_max'], g['feel'] or 'unknown', g['downbeats'], g['meter']['beats_per_bar'] or 'unknown'),
           'key     whole-song %s; per-section estimates (longest overlap) %s' % (g['key']['whole_song'] or 'unknown', ', '.join('%s %d%%' % (k['key'] or '?', round(100 * k['share'])) for k in g['key']['by_section']))]
    keys = {k['key'] for k in g['key']['by_section']} - {None}
    if g['key']['whole_song'] and keys and g['key']['whole_song'] not in keys:
        out.append('note    whole-song key %s is no section\'s estimate' % g['key']['whole_song'])
    out += ['form    %s   (%s)' % (' '.join(f['label'] + ('x%d' % f['count'] if f['count'] > 1 else '') for f in g['form']),
                                   ', '.join('%s=%s' % kv for kv in sorted(g['functions'].items())) or 'section roles unknown'),
            'level   loudest %s, quietest %s; section level = dB vs song median' % (rng(g['loudest']), rng(g['quietest'])),
            'genre   guess, %s: %s' % (g['genre_guess']['reliability'], ', '.join(g['genre_guess']['top']) or 'unknown'),
            'layers  ' + ', '.join('%s %s/%s' % (k, x['status'], x['reliability']) for k, x in L.items()) + '; melody = ' + g['melody_source'],
            'parts   % = ' + c['parts'],
            'melody  notes/s = ' + c['notes_per_note_s'],
            'bars    ' + c['bars']]
    for gid, p in g['grooves'].items():
        out.append('groove  %s kick %s snare %s' % (gid, grid(p['kick'], 'x'), grid(p['snare'], 'o')))
    out.append('')
    if not sh['sections']:
        out.append('sections unknown (no section layer in this result)')
    for s in sh['sections']:
        out.append('%s label %s%s | %s-%s | bars %s | key %s | level %s dB' % (
            s['id'], s['label'], ' (role %s)' % s['function'] if s['function'] else '', mm(s['start']), mm(s['end']),
            ('%d-%d' % tuple(s['bars']) if s['bars'] else 'none') if 'bars' in s else 'unavailable', s['key_estimate'] or '?', num(s['level_db'], '%+.1f')))
        extra = []
        if s.get('straddling_bars'):
            extra.append('straddling bars ' + ','.join(map(str, s['straddling_bars'])))
        if s.get('uncovered'):
            extra.append('no bar grid in ' + ', '.join(rng(u) for u in s['uncovered']))
        if extra:
            out.append('  grid   ' + '; '.join(extra))
        if s.get('key_changes'):
            out.append('  key    changes ' + ' '.join('%s %s' % (mm(t), k) for t, k in s['key_changes']))
        ch = s['chords']
        for ob in ch.get('outside_bars', []):
            out.append('  chords outside bars %s: %s' % (rng(ob['span']), ' '.join('%s %s' % (mm(t), c) for t, c in ob['changes']) or 'none'))
        if 'same_as' in ch:
            out.append('  chords = %s' % ch['same_as'])
        elif 'per_bar' in ch:
            out.append('  chords ' + ('| ' + ' | '.join('>'.join(x) if isinstance(x, list) else (x or '?') for x in ch['per_bar']) + ' |' if ch['per_bar'] else ch['status']))
        elif 'changes' in ch:
            out.append('  chords (no bar grid) ' + (' '.join('%s %s' % (mm(t), c) for t, c in ch['changes']) or 'empty'))
        else:
            out.append('  chords ' + ch['status'])
        m = s['melody']
        out.append('  melody ' + (m['status'] if m['status'] != 'detected' else '%d notes, %s-%s, peak %s at %s, %s, %.1f notes/s' % (
            m['notes'], m['low'], m['high'], m['peak'], mm(m['peak_time']), m['direction'] or 'direction unknown', m['notes_per_note_s'])))
        p = s['parts']
        if p['status'] != 'detected':
            out.append('  parts  ' + p['status'])
        else:
            d = ['%s rose past 50%%' % k for k in p.get('crossed_50_up', [])] + ['%s fell below 50%%' % k for k in p.get('crossed_50_down', [])]
            out.append('  parts  %s; other notes %s/s%s' % (' '.join('%s %s' % (k, '?' if p[k] is None else '%d%%' % round(100 * p[k])) for k in PARTS),
                                                             num(p['other_notes_per_s'], '%.1f'), ' (vs previous: ' + ', '.join(d) + ')' if d else ''))
        dr = s['drums']
        if dr['status'] in ('failed', 'unavailable'):
            out.append('  drums  ' + dr['status'] + (' (%d bars, none measured)' % dr['bars'] if dr.get('bars') else ''))
        else:
            cov = 'measured %d/%d bars' % (dr['measured'], dr['bars'])
            if dr['status'] == 'empty':
                out.append('  drums  %s; no kick or snare in any measured bar' % cov)
            else:
                out.append('  drums  %s; groove %s in %d/%d%s%s' % (cov, dr['groove'], dr['groove_bars'], dr['measured'],
                           '; %d other pattern%s' % (dr['other_patterns'], 's' * (dr['other_patterns'] > 1)) if dr['other_patterns'] else '',
                           '; %d empty bar%s' % (dr['empty_bars'], 's' * (dr['empty_bars'] > 1)) if dr['empty_bars'] else ''))
    dd = sh['drill_down']
    return '\n'.join(out + ['', 'zoom: ' + dd['zoom'],
                             'drill down: %s (layer %s; %s; %s)' % (dd['request'], dd['layers'], dd['range'], dd['paging'])]) + '\n'


def zoom_layers(v):
    """Per note layer: the row list, or a status string when the layer was not measured."""
    out = {}
    for name, k, lk in NOTE_LAYERS + (('hits', 'drum_hits', 'drums'),):
        st = layer_state(v, name if name != 'hits' else lk)[0]
        if st not in ('done', 'absent'):  # a skipped layer stores an empty row list: not measured, not empty
            out[name] = 'failed' if st == 'failed' else 'unavailable'
        else:
            out[name] = v[k] if isinstance(v.get(k), list) else 'unavailable'
    return out


def gaps_of(a, b, spans):
    """Every interval of [a, b) that no span covers (longer than EPS)."""
    out, t = [], a
    for s, e in sorted(spans):
        if min(s, b) - t > EPS:
            out.append([t, min(s, b)])
        t = max(t, e)
    if b - t > EPS:
        out.append([t, b])
    return out


def pitch_summary(rows):
    return {'count': len(rows), 'low': note(min(r['midi'] for r in rows)) if rows else None,
            'high': note(max(r['midi'] for r in rows)) if rows else None}


def hit_counts(rows, skip=()):
    return dict(sorted(collections.Counter(r['class'] for r in rows if r['class'] not in skip).items()))


def zoom(v, a, b, rid=None):
    """Bar-by-bar reading of [a, b). A note or hit belongs to the bar holding its onset; rows starting outside the
    span are left out. Notes sit on the bar grid's steps (each bar's own length), so tempo drift does not move them."""
    nsteps = meter(v)[1]
    secs = [dict(id='S%02d' % n, label=s['label'], function=s.get('function'), start=s['start'], end=s['end'])
            for n, s in enumerate(v.get('sections') or [], 1)]
    bars = (done(v, 'bars') or {}).get('rows')
    lay = zoom_layers(v)
    rows = {k: sorted((r for r in x if a <= r['time' if k == 'hits' else 'start'] < b),
                      key=lambda r: r['time' if k == 'hits' else 'start']) if isinstance(x, list) else x
            for k, x in lay.items()}
    status = {k: x if isinstance(x, str) else ('detected' if x else 'empty') for k, x in rows.items()}
    sec_of = lambda t: next((s['id'] for s in secs if s['start'] <= t < s['end']), None)
    zb, outside = [], {k: [] for k in rows}
    sel = [(i, r) for i, r in enumerate(bars or []) if r['start'] < b and r['end'] > a]
    starts = [r['start'] for _, r in sel]
    for i, r in sel:
        zb.append({'n': i + 1, 'start': r['start'], 'end': r['end'], 'section': sec_of((r['start'] + r['end']) / 2),
                   'partial': r['start'] < a or r['end'] > b, 'chord': r['split'] if r.get('split') else r.get('chord'),
                   'scale': r.get('scale'), 'degree': r.get('degree'), 'kick': r.get('kick'), 'snare': r.get('snare'),
                   **{k: [] for k in rows if not isinstance(rows[k], str)}})
    for k, x in rows.items():
        if isinstance(x, str):
            continue
        for r in x:
            t = r['time' if k == 'hits' else 'start']
            j = bisect.bisect_right(starts, t) - 1
            if j >= 0 and t < zb[j]['end'] and round((t - zb[j]['start']) / (zb[j]['end'] - zb[j]['start']) * nsteps) >= nsteps:
                j = j + 1 if j + 1 < len(zb) and abs(zb[j + 1]['start'] - zb[j]['end']) <= EPS else -1  # next bar's step 0
                if j >= 0:
                    zb[j][k].append(r)
                    continue
            (zb[j][k] if j >= 0 and t < zb[j]['end'] else outside[k]).append(r)
    if bars and not any(r.get('chord') is not None for r in bars) and v.get('chords'):
        for z in zb:
            z['chords_timed'] = timed(v['chords'], max(z['start'], a), min(z['end'], b))
    for z in zb:
        ln = z['end'] - z['start']
        step = lambda t: min(nsteps - 1, max(0, int(round((t - z['start']) / ln * nsteps))))
        for k in ('melody', 'bass'):
            if k in z:
                z[k] = [[r.get('note') or note(r['midi']), step(r['start']), max(1, int(round((r['end'] - r['start']) / ln * nsteps)))] for r in z[k]]
        if 'other' in z:
            z['other'] = pitch_summary(z['other'])
        if 'hits' in z:
            z['hits'] = hit_counts(z['hits'], ('kick', 'snare') if z['kick'] is not None else ())
    spans = uncovered(a, b, [(r['start'], r['end']) for _, r in sel]) if bars is not None else [[a, b]]
    gaps = gaps_of(a, b, [(r['start'], r['end']) for _, r in sel]) if bars is not None else [[a, b]]
    out = {'spans': spans, 'chords': [c for u in gaps for c in timed(v.get('chords') or [], u[0], u[1])]}
    for k, x in outside.items():
        if isinstance(rows[k], str):
            continue
        if k == 'other':
            out[k] = pitch_summary(x)
        elif k == 'hits':
            out[k] = hit_counts(x)
        else:
            out[k] = [[r.get('note') or note(r['midi']), round(r['start'], 2), round(r['end'] - r['start'], 2)] for r in x]
    bst = layer_state(v, 'bars')
    return {'format': 'music-lens-sheet-zoom/1', 'result_id': rid, 'worker': (v.get('tool') or {}).get('version'),
            'duration': v['duration'], 'span': [a, b], 'meter': dict(zip(('beats_per_bar', 'steps'), meter(v))),
            'conventions': {
                'bars': 'bars overlapping the span, numbered as in the full sheet; partial = the bar reaches outside the span',
                'notes': 'a note belongs to the bar holding its onset (an onset in a bar\'s last half step goes to step 0 of the next bar, as in the kick/snare grid): [name, step, length], %d steps per bar of that bar\'s own length; rows starting outside the span are left out' % nsteps,
                'drums': 'kick and snare are the bar row\'s %d-step pattern; hits counts the other drum classes in the bar' % nsteps,
                'outside_bars': 'rows whose onset lies in no bar: notes [name, start s, duration s]; spans = gaps of %.0f s or more' % GAP,
                'status': 'detected = rows in the span; empty = measured, none in the span; unavailable = not measured; failed = the layer failed'},
            'layers': dict(status, bars={'status': bst[0], 'reliability': bst[1]},
                           chords={'status': 'done' if v.get('chords') else 'absent', 'reliability': rel(v.get('chords_reliability')) if v.get('chords') else 'unknown'}),
            'melody_source': melody_source(done(v, 'melody')),
            'sections': [s for s in secs if s['start'] < b and s['end'] > a],
            'bars': zb if bars is not None else None, 'outside_bars': out}


def render_zoom(z):
    mm = lambda t: '%d:%05.2f' % (t // 60, t % 60)
    grid = lambda steps, ch: ''.join(ch if i in steps else '.' for i in range(z['meter']['steps']))
    notes = lambda xs: ' '.join('%s@%d+%d' % tuple(x) for x in xs) or 'none'
    pitch = lambda p: '%d note%s, %s-%s' % (p['count'], 's' * (p['count'] > 1), p['low'], p['high']) if p['count'] else 'none'
    hits = lambda h: ', '.join('%s %d' % kv for kv in h.items()) or 'none'
    L, c = z['layers'], z['conventions']
    a, b = z['span']
    out = ['MUSIC LENS SHEET ZOOM (%s) | result %s | %s-%s of %s | worker %s' % (z['format'], (z['result_id'] or 'unknown')[:12], mm(a), mm(b), mm(z['duration']), z['worker']),
           'notes   ' + c['notes'] + '.',
           'drums   ' + c['drums'] + '.',
           'status: ' + c['status'] + '.',
           'layers  ' + ', '.join('%s %s' % (k, L[k]) for k in ('melody', 'bass', 'other', 'hits'))
           + '; bars %s/%s; chords %s/%s' % (L['bars']['status'], L['bars']['reliability'], L['chords']['status'], L['chords']['reliability'])
           + '; melody = ' + z['melody_source'],
           'sections ' + ('; '.join('%s label %s%s %s-%s' % (s['id'], s['label'], ' (role %s)' % s['function'] if s['function'] else '', mm(s['start']), mm(s['end'])) for s in z['sections']) or 'none in the span')]
    if z['bars'] is None:
        out.append('bars    unavailable in this result')
    for r in z['bars'] or []:
        ch = r['chord']
        ch = '>'.join(ch) if isinstance(ch, list) else (ch or '?')
        if 'chords_timed' in r:
            ch = 'chords (timed, no bar chords) ' + (' '.join('%s %s' % (mm(t), x) for t, x in r['chords_timed']) or 'none')
        extra = ', '.join(x for x in ('scale %s' % r['scale'] if r.get('scale') else '', 'degree %s' % r['degree'] if r.get('degree') else '') if x)
        out += ['', 'bar %d%s | %s-%s | %s | %s%s' % (r['n'], ' (partial)' if r['partial'] else '', mm(r['start']), mm(r['end']), r['section'] or 'no section', ch, ' (%s)' % extra if extra else '')]
        if r['kick'] is None or r['snare'] is None:
            dr = 'kick/snare not measured'
        else:
            dr = 'kick %s snare %s' % (grid(r['kick'], 'x'), grid(r['snare'], 'o'))
        if 'hits' in r:
            dr += '; %s %s' % ('other hits' if r['kick'] is not None else 'hits', hits(r['hits']))
        out.append('  drums  ' + dr)
        for k in ('melody', 'bass'):
            if k in r:
                out.append('  %-6s %s' % (k, notes(r[k])))
        if 'other' in r:
            out.append('  other  ' + pitch(r['other']))
    o = z['outside_bars']
    has = o['chords'] or any(o.get(k) for k in ('melody', 'bass', 'hits')) or (o.get('other') or {}).get('count')
    if has:
        if z['bars'] is None:
            out += ['', 'rows by time (onset time+duration s):']
        else:
            out += ['', 'outside the bar grid%s (onset time+duration s):' % (', gaps %s' % ', '.join('%s-%s' % (mm(u[0]), mm(u[1])) for u in o['spans']) if o['spans'] else '')]
        if o['chords']:
            out.append('  chords ' + ' '.join('%s %s' % (mm(t), x) for t, x in o['chords']))
        for k in ('melody', 'bass'):
            if o.get(k):
                out.append('  %-6s %s' % (k, ' '.join('%s %s+%.2f' % (n, mm(t), d) for n, t, d in o[k])))
        if (o.get('other') or {}).get('count'):
            out.append('  other  ' + pitch(o['other']))
        if o.get('hits'):
            out.append('  hits   ' + hits(o['hits']))
    return '\n'.join(out) + '\n'


if __name__ == '__main__':
    stem = os.path.basename(sys.argv[1])[:-5]
    rid = stem if re.fullmatch('[0-9a-f]{64}', stem) else None
    v = json.load(open(sys.argv[1]))
    sh = zoom(v, float(sys.argv[4]), float(sys.argv[5]), rid) if len(sys.argv) > 4 else build(v, rid)
    json.dump(sh, open(sys.argv[2], 'w'), separators=(',', ':'))
    sh = json.load(open(sys.argv[2]))
    open(sys.argv[3], 'w').write(render_zoom(sh) if len(sys.argv) > 4 else render(sh))
