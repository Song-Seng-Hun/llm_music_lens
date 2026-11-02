<p align="center"><b>한국어</b> · <a href="README.en.md">English</a> · <a href="README.ja.md">日本語</a></p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.svg">
    <img src="assets/banner-light.svg" width="640" alt="픽셀아트 배너. 분석 화면에서 재생 막대가 120 BPM으로 파형·박 표시·피아노롤을 지나가며 박과 음표를 밝히고 C·Am·F·G 코드를 띄운 뒤, BPM 120과 C 장조를 보고합니다. 아래에서는 스피커가 박자에 맞춰 울리고, 헤드폰을 쓴 Claude가 고개를 끄덕이다 분석 결과를 받습니다.">
  </picture>
</p>

# Music Lens — 음악 분석실

음원에서 박자·화음·음표·가사·에너지 단서를 뽑아, AI(LLM)가 근거를 들어 음악을 분석할 수 있게 넘겨 주는 프로그램입니다.

## 이게 뭔가요

곡을 넣으면 "몇 BPM인지, 박이 어디에 있는지, 어떤 코드와 음이 언제 울렸는지, 가사 단어가 박의 어디에 걸리는지"를 시간과 함께 정리합니다. 결과는 화면으로 보고 원음과 맞춰 볼 수 있고, JSON·Markdown·"AI 팀 전달문"으로 내보내거나 MCP로 AI에게 바로 넘길 수 있습니다. 음악 분석의 근거, 작곡 참고, 스타일 읽기, 영상 연출에 쓰려고 만들었습니다.

두 가지 방식으로 씁니다.

- **웹 앱**: 브라우저에서 파일을 열어 분석합니다. 브라우저에서 분석한 음원은 밖으로 나가지 않습니다.
- **MCP 서버**: 서버가 음원을 분석하고, 연결된 AI(예: ChatGPT)가 대화 안에서 결과를 읽습니다. 개인 모델 API 키가 필요 없습니다. 사이트 게시와 팀 연결은 아직 검증 전입니다.

## 주요 기능

- **정밀 분석**: BPM과 박 격자, 절반·두 배 템포 후보, 저·중·고 대역 어택, 코드·조성 후보, 여러 음의 시작·끝·음높이, 에너지 변화
- **솔로 보컬 음표 (선택)**: 반주 없는 노래에 맞춘 SOME 모델로 음표와 연속 음높이를 뽑습니다.
- **가사 단어 전사 (선택)**: Whisper로 단어마다 시작·끝 시각을 추정하고, 가까운 박과 같은 시각의 음·코드를 찾아 줍니다.
- **원본 대조**: 피아노 롤, 단어·변화 구간 클릭, 원음 재생과 이동
- **AI에게 넘기기**: JSON, Markdown 요약, "AI 팀 전달문", MCP 도구
- **서버 분석 도구 (MCP)**: 박·BPM, 파형 다이내믹스, 스펙트럼 질감, 박 대비 어택, 분리된 보컬의 음높이, 곡 전체의 전환 후보, 여러 구간을 이은 시간축
- **저장과 불러오기**: 로그인한 사용자별 보관함(UTF-8 JSON 1.9MB까지). 음원은 저장하지 않습니다.
- **Mac 분석 서버 (`lan/`)**: 같은 공유기 안의 AI 팀원이 Mac의 분석 기능을 서명된 요청으로 쓰게 하는 별도 서버
- **장르 추정 (Mac 분석 서버, 선택)**: 곡 전체와 30초 구간마다 Discogs 기준 세부 장르(예: Pop---J-pop)와 큰 갈래(Pop, Rock 등)를 점수와 함께 추정합니다.

## 빠른 시작

Windows에서는 이 폴더의 `Start-MusicLens.cmd`를 실행하고 `http://127.0.0.1:5173/`을 엽니다. 실행한 터미널에서 Ctrl+C를 누르면 멈춥니다.

처음 준비할 때는 Node.js 22.13 이상과, 저장소에 접근할 수 있는 GitHub 계정이 필요합니다.

```sh
git clone https://github.com/Song-Seng-Hun/llm_music_lens.git
cd llm_music_lens/web
npm run install:ci
npm run build
npm run db:local
npm run dev -- --host 127.0.0.1
```

화면이 뜨면 파일을 고르고 **정밀 분석 시작**을 누릅니다. 파일이 없으면 16초짜리 합성 데모로 먼저 둘러볼 수 있습니다.

## 사용 예

### 곡 하나를 분석해서 AI에게 넘기기

1. 파일을 고르고 정밀 분석을 시작합니다. 가사가 필요하면 Whisper 모델과 언어를 고릅니다.
2. 피아노 롤과 단어를 눌러 원음과 맞는지 확인합니다.
3. "AI 팀 전달문"이나 JSON을 내보내 AI와의 대화에 넘깁니다. 화면 요약과 전달문은 전체 JSON을 대신하지 않습니다.

### 장르 추정 결과 읽기 (Mac 분석 서버)

Mac 분석 서버에 리듬 분석을 함께 요청하면(`rhythm=1`) 결과에 `genre`가 붙습니다.

- **곡 전체**: Discogs 기준 세부 장르 상위 5개(`Pop---J-pop`처럼 "큰 갈래---세부 장르")와 큰 갈래 상위 5개(Pop, Rock, Electronic 등)
- **30초 구간별**: 구간마다 세부 장르 상위 3개. 시간축 조회(`layer=genre`)로 받습니다.
- **점수 읽는 법**: 장르마다 따로 매긴 모델 추정이라 합이 1이 아니고 확률도 아닙니다. 숫자 크기보다 순위를 보세요.
- 1등은 네 곡 중 한두 곡꼴로 틀리지만, 정답은 대개 3등 안에 있습니다. Pop처럼 경계가 흐린 장르와 30초 구간별 결과는 덜 믿을 만합니다. 측정 결과는 [장르 추정 정확도](docs/genre-accuracy.md)에 있습니다.

### AI가 서버에서 직접 분석하기 (MCP)

사이트를 게시하고 AI에 연결하면, AI가 대화 중에 이 도구들을 부릅니다.

| MCP 도구 | 하는 일 |
|---|---|
| `analyze_music_timing` | 1~30초 PCM의 박·마디 첫박·BPM, 파형, 스펙트럼 질감, 어택, (분리된 보컬이면) 음높이 |
| `analyze_music_structure` | 10분까지의 곡에서 구간 전환 후보와 소리 크기·음색 변화 |
| `compose_music_timeline` | 분석한 여러 구간의 박과 음높이를 곡 전체 시간으로 이어 붙이기 |
| `list_music_reports` / `get_music_report` / `get_music_timeline` | 저장된 분석 결과 목록, 요약, 시간 범위별 상세 |

## 현재 상태와 한계

- 입력 파일은 12MiB·10분까지이고, 브라우저가 디코딩할 수 있는 MP3/WAV/FLAC/OGG/M4A/AAC/WEBM입니다.
- 모든 결과는 `accuracy: unvalidated`입니다. 합성음과 공개 음성, 몇 곡으로 한 측정은 실곡 전반의 정확도를 뜻하지 않습니다.
- 음표에는 반주·배음·오검출이 섞일 수 있어 실제 악보가 아닙니다. 코드와 조성은 근거가 부족하면 보류합니다.
- 가사 전사는 노래·빠른 랩·여러 목소리에서 단어를 지어내거나 빠뜨릴 수 있습니다.
- 악기·감정은 자동으로 판단하지 않습니다. 장르는 Mac 분석 서버의 리듬 층에서만 모델 추정으로 나오며, 웹 앱과 MCP 사이트 도구에는 없습니다. 스타일 해석은 출처와 시간 범위를 든 AI의 몫입니다.
- 사이트 게시, 팀 연결, 사용자별 보고서 조회는 실제 인증 연결로 아직 확인하지 않았습니다.

## 사용 모델과 라이선스

| 구성 | 쓰는 곳 | 라이선스 |
|---|---|---|
| [Beat This!](https://github.com/CPJKU/beat_this) small0 (ONNX로 변환해 `web/models/beat-this/`에 포함) | 서버 박·BPM | MIT |
| [Spotify Basic Pitch](https://github.com/spotify/basic-pitch-ts) (모델 포함) | 브라우저 음표 | Apache-2.0 |
| [Whisper](https://huggingface.co/Xenova/whisper-base) (`Xenova/whisper-base`·`whisper-small`, 필요할 때 브라우저가 내려받음) | 가사 단어 전사 (선택) | Apache-2.0 (모델 카드 기준) |
| [SOME](https://github.com/openvpi/SOME) (모델은 따로 준비) | 솔로 보컬 음표 (선택) | 코드 MIT, 모델 CC BY-NC-SA 4.0 |
| [Essentia](https://essentia.upf.edu/)와 TempoCNN 모델 (Mac 서버에 따로 설치) | 선택 리듬 층 | Essentia AGPL-3.0, 모델 조건은 확인 안 됨 |
| Essentia Discogs-EffNet·genre_discogs400 모델 (Mac 서버에 따로 설치) | 선택 리듬 층의 장르 추정 | CC BY-NC-ND 4.0 |
| Transformers.js · ONNX Runtime Web · TensorFlow.js | 브라우저에서 모델 실행 | Apache-2.0 · MIT · Apache-2.0 |

- SOME 모델(CC BY-NC-SA 4.0)과 Discogs 장르 모델(CC BY-NC-ND 4.0)에는 비상업적 이용 조건이 있습니다.
- 포함된 구성의 라이선스 원문은 [web/public/licenses/](web/public/licenses/README.md)와 [web/models/beat-this/](web/models/beat-this/README.md)에 있습니다.

## 더 보기

자세한 문서는 영어입니다.

- [전체 안내](docs/guide.md): 서버 도구별 설명, 브라우저 분석 단계, MCP 연결, 입력·정확도 범위 전체, 개발 메모
- [연구 상태와 한계](docs/research-status.md): 측정 요약
- 서버 도구 문서: [박·BPM](docs/server-timing.md) · [파형](docs/waveform-dynamics.md) · [스펙트럼 질감](docs/spectral-texture.md) · [박 대비 어택](docs/attack-beat.md) · [보컬 음높이](docs/server-vocal-pitch.md) · [곡 구조](docs/music-structure.md) · [시간축](docs/music-continuity.md)
- [솔로 보컬 분석 준비](docs/vocal-specialist.md)
- [Mac 분석 서버](lan/README.md)
- [장르 추정 정확도](docs/genre-accuracy.md)
- 개발 확인(`web/`에서): `npm run typecheck`, `npm test`, `npm run build`
