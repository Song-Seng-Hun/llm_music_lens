# Genre estimate accuracy

Measured 2026-10-31 on the Mac (Claude-Mac), before adopting the genre layer of the Mac analysis server (`lan/`, rhythm worker 0.5.0). Output format, failure codes and model pins: [lan/README.md, Genre](../lan/README.md#genre-worker-050).

## Setup

- Test audio: GTZAN, 20 tracks per genre x 10 genres (Hugging Face `danilotpnta/GTZAN_genre_classification`, fixed random seed 0), and FMA-small validation shard 0 (`rpmon/fma-genre-classification`, 399 tracks, 8 genres). First 30 s of each track, 16 kHz mono. The audio stays local and is not in this repository.
- Discogs (adopted): Essentia `discogs-effnet-bs64-1` embedding + `genre_discogs400-discogs-effnet-1` head, mean of the per-patch sigmoid activations. The 400 styles were mapped to each dataset's genre names by a fixed rule (score = maximum over a set of styles, e.g. GTZAN `metal` = Rock styles containing "Metal"), written before any result was seen.
- EmbeddingGemma 2 (not adopted): `google/embeddinggemma-2`, audio-only, cosine similarity between the audio embedding and the genre name text. Three prompt templates were fixed up front: `{}`, `{} music`, `task: classification | query: {} music`.
- FMA "Instrumental" and "International" have no Discogs equivalent, so the comparison uses the other 6 FMA genres (311 tracks).

## Results

Top 1 = the first pick is the labelled genre; top 3 = the labelled genre is among the first three.

| Test set | Chance | Discogs top 1 / top 3 | EmbeddingGemma 2, best template, top 1 / top 3 |
|---|---|---|---|
| GTZAN 10 genres, 200 tracks | 0.10 | **0.745 / 0.965** | 0.495 / 0.830 (`{} music`) |
| FMA 6 genres, 311 tracks | 0.17 | **0.576 / 0.823** | 0.463 / 0.733 (`task: classification \| query: {} music`) |

Discogs per genre, top 1:

- GTZAN: blues 0.55, classical 1.00, country 0.60, disco 0.70, hiphop 0.85, jazz 0.85, metal 0.45, pop 0.65, reggae 0.90, rock 0.90
- FMA: Electronic 0.88, Experimental 0.60, Folk 0.47, Hip-Hop 0.71, Pop 0.04, Rock 0.82

EmbeddingGemma 2 was also sensitive to the prompt: the best template differed per dataset, and single genres swung between 0.00 and 0.90 (rock 0.00–0.18). Asking it over all 400 Discogs style names and then applying the same mapping was worse (top 1 0.215 GTZAN, 0.289 FMA).

## Limits

- GTZAN tracks are commercial recordings and may overlap Discogs' training data; FMA (Creative Commons indie music) is less likely to, and Discogs still leads there.
- FMA "Pop" is close to zero for both models; the label is known to be loose.
- Discogs tags genres per release, so the 30 s window genres are less reliable than the whole-song result. Window accuracy was not measured.
- Not measured systematically on the team's own songs (J-pop, Vocaloid, anime songs). Japanese and Korean pop styles exist among the 400 (`Pop---J-pop`, `Pop---K-pop`, `Pop---City Pop`), but their accuracy is not measured.

## Spot check on two released J-pop songs (2026-10-31)

Whole songs, the deployed worker 0.5.0; one run over the LAN API, one on the Mac. Song names are left out on purpose.

| Song | J-pop rank (whole song) | Top style and score | 30 s windows with J-pop first |
|---|---|---|---|
| Synth-driven J-pop, 261 s | 1st (0.823) | `Pop---J-pop` 0.823 | 9 of 9 |
| J-pop ballad with rock band parts, 274 s | 5th (0.072) | `Pop---K-pop` 0.184 | 1 of 9 (K-pop first in 5) |

- When the model is confident (top score well above 0.5) it was right; when the top score stayed under about 0.2 it mixed J-pop with K-pop and nearby rock/ballad styles. Two songs only: a hint, not a measured rule.
- Read a low top score as "unclear" rather than as the answer.
- After implementation, the worker's top 3 styles and scores on 8 of these clips matched the reference measurement to 3 decimals; a 600 s input took 14.2 s for the whole rhythm worker.
