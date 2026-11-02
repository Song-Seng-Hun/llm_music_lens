# Server spectral texture

`analyze_music_timing` returns `acoustics` alongside its existing `timing`,
`vocalTiming` and `waveform`. It reuses the beat frontend's FFT: no extra model,
API, browser, inference pass or dependency. No audio or result is saved.
This is new server evidence, not improved beat recognition or automatic
instrument, genre, emotion or artistic-quality recognition.

## Measurements

At most30one-second windows cover the supplied1–30second excerpt. The final
window ends at its actual duration. Times are excerpt-relative; add
`source.offsetSeconds` for song time. `sourceKind` is the same caller declaration
as the waveform field; `isolated-vocal` does not verify or separate a voice.

| Field | Meaning |
|---|---|
| `bandBalance.low` | Share of measured power below180Hz |
| `bandBalance.mid` | Share from180Hz inclusive to2000Hz exclusive |
| `bandBalance.high` | Share from2000Hz inclusive up to the last interior FFT bin |
| `centroidHz` | Magnitude-weighted frequency center, averaged over active frames |
| `rolloff85Hz` | First frequency containing85% of frame power, averaged over active frames |
| `flatness` | Geometric/arithmetic mean power ratio; larger means a flatter spectrum, not a noise probability |
| `activeFraction` | Active frame centers divided by frame centers in the window; not exact sound duration |

Bands sum to approximately1after rounding. They sum active-frame power before
normalizing; they are not a mean of per-frame ratios. Changes across windows can
support an interpretation such as “the second excerpt has more high-band energy.”
They cannot alone identify a hi-hat, distorted guitar, breath, vowel or singer.
Use `waveform` for existing energy/entry/rise/fall observations, and `timing` for
model-estimated beats. No server attack-rate or drum label is fabricated.

## Definitions and limits

Input remains signed16PCM,22050Hz mono. The existing1024-point periodic-Hann
FFT uses centered reflected frames every441samples (20ms). Interior positive
bins1–511are used: DC and Nyquist are excluded. Magnitudes are converted from
the beat model's `/sqrt(1024)` normalization to `/1024` for the shared descriptor
floors. The128log-mel model features are not interpreted as power.

Only frames whose raw reflected-window RMS is at least0.0001(-80dBFS) contribute.
A one-second bin below the same raw-RMS gate has null spectral values even if
a centered frame sees its active neighbor. Digital silence and below-gate
signals therefore have unknown spectral values, not a zero-frequency estimate.
Log-power floor1e-20, total-power threshold1e-20 and magnitude threshold1e-10
preserve the existing shared implementation. Descriptors round to four decimals;
the server's final window endpoint retains the exact PCM duration.

Frame support is approximately46.4ms and crosses one-second boundaries; each
frame is assigned by its center. Padding and excerpt edges can affect texture.
Frequencies above11025Hz and stereo spatial information are unavailable.
The saved browser report uses4096-point symmetric-Hann, zero-padded frames;
its values are not numerically interchangeable with this server frontend.
Extracting the shared arithmetic preserves that browser path's prior values.

## Evidence

Frozen scope, fixtures, numerical tolerances and cost guards are recorded in
the local work log, with local raw receipts under `evidence/spectral-texture-12/`.
Independent NumPy FFT arithmetic checks and source/timing parity establish only
these measurements. They do not establish broad real-song perceptual accuracy.
Hosted Sites execution and whole-isolate peak memory remain unverified.

2026-10-14 confirmation on3fixed real recordings:71windows/639scalar comparisons
meet frozen tolerances (centroid maximum difference0.0001Hz; other rounded
spectral fields match). Existing449,920model-input floats and all prior MCP
outputs remain exact except diagnostic timers. Ten complete browser fixture
outputs remain exact. Known-tone/mix/noise/quiet checks and the actual built
MCP pass; full suite87/87, typecheck/build pass, lint0errors/3existing warnings.

Added whole-MCP payload6,704–13,216bytes passes the30KB guard. One Node frontend
pair per recording passes the≤100ms added/30s guard; new aggregation measured
14.83–39.01ms separately. Whole-request differences were-102.59/-75.66/+697.21ms;
single pairs cannot resolve latency impact, and no speedup is claimed.

Independent review found rounding could collapse tiny final windows. Server
endpoints now retain the exact PCM duration; original failed output and the
oracle's matching endpoint defect remain recorded. Browser formatting and all
spectral values stay unchanged. Close: meaningful new measured capability;
end-to-end latency effect remains inconclusive under this budget.
