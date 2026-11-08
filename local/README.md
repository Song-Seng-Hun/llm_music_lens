# Local Music Lens (macOS)

Local Music Lens listens to a song on your Mac and writes down when each sung word starts and ends. If you want, it
also scores the singer's voice emotion or the music's mood, in short windows along the song. Everything runs on the
Mac, one job at a time; nothing is uploaded or downloaded.

## What you need

- A Python 3.12 environment with MLX, as recorded in `requirements-macos.lock`.
- A Whisper model folder. We use large-v3 (`mlx-community/whisper-large-v3-mlx`, MIT); large-v3-turbo also works.
- For emotion or mood: a second Python environment (`requirements-emotion-macos.lock`) and its model files.

The tool never installs packages or downloads models. You prepare these once.

## Analyze a song

```sh
python3 -I music_lens_local.py --audio-root /abs/audio --result-root /abs/results --model-dir /abs/whisper-model analyze song.wav --language en
```

The input is a mono 16 kHz 16-bit WAV of up to 10 minutes inside the audio folder. Choose the language (`en`, `ko`,
`ja` or `auto`). For emotion, add `--emotion-python` and `--emotion-assets` before `analyze`, and `--emotion-mode voice`
(the singer's voice) or `--emotion-mode music` (the music's mood) after it.

## What you get

A report in the results folder, `RESULT_ID.json`, with each word's start and end and, if asked, the emotion or mood
windows. `report RESULT_ID` prints a summary, `timeline RESULT_ID --layer words` pages through the rows, and
`export RESULT_ID` writes a file the Music Lens site can import.

A finished run can still be partial: if one layer fails, the others are kept. Check the state and warnings before you
trust a result. Scores are model estimates: the voice models are not validated on singing, and music-mood accuracy is
not measured. Whisper can also invent or skip words in songs.

Models and their files, result states, caching, recovery, version notes and tests: [docs/internals.md](docs/internals.md).
