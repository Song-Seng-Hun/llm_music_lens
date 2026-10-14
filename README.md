# Music Lens — 음악 분석실

음원에서 시간·박자·화음·음표·단어·에너지 단서를 뽑아 **LLM의 음악 분석 근거**로 제공하는 프로그램입니다. 작곡 레퍼런스, 스타일 해석, 영상 연출 등에서 같은 자료를 재사용할 수 있습니다. ChatGPT Sites/Vinext 기반이며, **개인 모델 API 키 없이 브라우저에서 계산**합니다. Sites 게시와 실제 팀 연결은 아직 남아 있습니다.

프로젝트 저장소: [Song-Seng-Hun/llm_music_lens](https://github.com/Song-Seng-Hun/llm_music_lens). 현재 저장소에는 소스·테스트·설치 스크립트·라이선스 안내를 보관합니다. 모델, 음원, 정답 주석, 실행 로그와 실험 원자료는 커밋하지 않습니다. 이 문서의 `evidence/` 및 연구용 `examples/` 링크는 기존 로컬 작업 폴더의 기록이며 새 clone에는 없습니다. 저장소에서 읽을 수 있는 측정 요약은 [연구 상태와 한계](docs/research-status.md)에 있습니다.

## 바로 실행

이 폴더의 `Start-MusicLens.cmd`를 실행하고 `http://127.0.0.1:5173/`를 엽니다. 종료는 실행 터미널에서 Ctrl+C. 자동 시작·상시 서비스·방화벽 변경은 없습니다.

```powershell
& .\Start-MusicLens.ps1
```

Node.js 22.13 이상이 필요합니다. 새 작업 폴더에서는 다음 순서로 준비합니다. 저장소 접근 권한이 있는 GitHub 계정을 사용하세요.

```sh
git clone https://github.com/Song-Seng-Hun/llm_music_lens.git
cd llm_music_lens
npm run install:ci
npm run build
npm run db:local
npm run dev -- --host 127.0.0.1
```

빌드가 기본 음표 모델·WASM·분석 worker를 설치된 의존성에서 준비합니다. 선택 기능인 SOME 독창 모델은 별도이며, 필요할 때 [준비 절차](docs/vocal-specialist.md#prepare)를 실행한 뒤 다시 빌드하세요. `.openai/hosting.json`은 비밀이 없는 Sites 바인딩 선언이므로 저장소에 포함하며, 실행 프로필·로컬 DB·환경 파일은 포함하지 않습니다. Windows npm shim이 실패하면 설치된 Node의 실제 `npm-cli.js`로 같은 스크립트를 호출할 수 있습니다.

## 지금 되는 일

**독창 음표 정확도 개선:** 정밀 분석에서 **음표 대상 → 독창 · 보컬만 (SOME)**을 선택할 수 있습니다. 반주 없는 보컬의 소수 음높이·시작/끝을 보존하며 신뢰도 미제공은 null로 기록합니다. 미사용 독창 두 곡의 음표 F1은 48.56%→71.04%였습니다(시작점 50 ms·음높이 50 cents, 전체 노래 일반화 아님). 현재 PC에 모델을 준비했고, 다른 PC의 설치·라이선스·정확도 범위는 [독창 분석 안내](docs/vocal-specialist.md)에 있습니다. 기존 다성 분석은 기본값으로 유지됩니다.

1. **정밀 분석:** 파일 선택 → 정밀 분석 시작. BPM·박 그리드·반속/배속 후보, 저/중/고음 타격, 화음·조성 후보, 다성 음표의 시작/끝/MIDI 높이와 에너지 변화를 계산합니다. 파일이 없으면 16초 합성 데모로 실행할 수 있습니다.
2. **선택적 단어 전사:** Whisper Base/Small, 언어를 선택하면 단어별 시작·끝 시각을 추정합니다. 가사·랩 분석의 초안이며 반주와 발음에 따라 틀릴 수 있습니다.
3. **분석 단서:** 박 위 강세·엇박·대역별 타격 후보, 빌드업·드롭 복귀·에너지 하강, 5초당 단어 수와 한글 문자 음절 수, 관측 음역·음 클래스 분포를 제공합니다. 단어에서 가장 가까운 박의 시차와 같은 시각의 음표/화음도 조회합니다.
4. **원음 대조:** 피아노롤, 단어·변화 구간 클릭, 원음 재생/탐색. 저장한 결과에 같은 음원을 다시 연결할 때 파일 SHA-256을 대조합니다. 취소하면 기존 결과를 보존합니다.
5. **AI 팀 전달:** JSON, Markdown 요약, ‘AI 팀 전달문’ 또는 아래 MCP. 시간 구간별 근거, 모델·방법, 잘림과 실패/미실행을 함께 전달합니다. 화면의 요약이나 전달문은 전체 JSON의 대체물이 아닙니다.
6. **저장/가져오기:** 로그인한 사용자별 보관함, 최대 1.9 MB UTF-8 JSON. 원음은 저장하지 않습니다. 저장·가져온 자료는 클라이언트 제공 데이터이며 독립 검증으로 승격하지 않습니다.
7. **빠른 모드 / YouTube 참고:** 빠른 모드는 모델 없는 기존 신호 분석입니다. YouTube는 참고 주소 확인과 원본 링크만 지원하며 주소만으로 음원을 내려받거나 분석하지 않습니다.

**추가된 귀 역할:** 템포 후보를 실제 타격 포착률·그리드 지지와 비교하고, 모호하거나 변하는 템포는 전곡 고정 박을 보류합니다. 긴 무음에는 박을 억지로 채우지 않습니다. ‘분석 단서’에서 1초별 스펙트럼 중심/평탄도, 대역 에너지 비중, 타격 밀도, RMS를 볼 수 있으며 MCP와 전달문에도 포함됩니다. 구조적 변화는 2초 단위 에너지로 판단해 개별 타격의 감쇠와 구분합니다.

음표 모델과 WASM 실행 파일은 사이트에서, 선택한 가사 모델은 Hugging Face에서 내려받습니다. 음원은 브라우저를 떠나지 않습니다. 첫 가사 실행은 수백 MB급 다운로드와 수 분 이상 계산이 필요할 수 있습니다. 페이지를 여는 것만으로 모델을 내려받지는 않습니다.

## 키 없이 ChatGPT와 연결하는 방식

사이트는 계산과 근거 조회를 맡고, **연결된 ChatGPT가 대화 안에서 해석**합니다. Sites의 공식 MCP 연결은 모델 API 키와 별개입니다. 사이트가 ChatGPT의 내부 모델을 직접 호출하는 기능은 구현하지 않았습니다.

| MCP 도구 | 읽기 전용 동작 |
|---|---|
| list_music_reports | 인증된 사용자의 최근 분석 최대 30개 |
| get_music_report | 요약, 출처, 음역/음 클래스, 템포 근거, 음향 요약, 모델·단계 상태와 한계 |
| get_music_timeline | 최대 120초 범위의 단어·박 시차·동시 음표/화음·타격·에너지·변화·밀도·1초 음색/대역 비중·구간 템포; 레이어당 200개, total/truncated 표시 |

Sites 게시 후 플랫폼이 제공하는 개인 플러그인으로 설치/연결하고 실제 조회를 확인해야 합니다. 플랫폼 관리 연결에 임의의 로컬 OAuth나 별도 모델 키 설정을 추가하지 않습니다. 각 AI가 같은 사용자 보고서를 볼 수 있는지는 실제 인증 연결로 확인해야 합니다. 사용자 없는 service access는 사용자별 보고서 접근 권한이 아닙니다.

현재 공유 URL은 없습니다. 게시 전에는 파일로 전달할 수 있습니다. `skills/music-lens/SKILL.md`는 사용 안내 초안이며 전역 설치하지 않았습니다.

로컬 화면의 ‘로컬 로그인’은 Sites 스타터의 개발용 가상 계정 Seedy이며 실제 ChatGPT 계정 로그인이 아닙니다. 개발 서버를 다른 PC에 공개하거나 로컬 개발 쿠키를 팀용 인증으로 배포하지 마세요.

## 입력과 정확도의 범위

- 파일: 최대 12 MiB / 10분. MP3/WAV/FLAC/OGG/M4A/AAC/WEBM 중 브라우저가 디코딩하는 형식. 리듬·다성은 22.05 kHz 모노, 독창 SOME은 44.1 kHz 모노, 전사와 빠른 모드는 16 kHz. 재생은 원본입니다.
- 리듬: spectral flux와 실제 타격 시각의 일치도를 비교한 박 후보. supported는 관측 타격을 설명하는 반복 주기라는 뜻이며 음악적 정답 확률이 아닙니다. ambiguous/variable/insufficient는 BPM·전곡 그리드를 보류하고 후보/구간 근거를 남깁니다. 반속·배속은 여전히 가능하며 강세/엇박도 박자표·마디 첫 박 판정이 아닙니다.
- 음색: 혼합음의 스펙트럼 중심·85% 파워 rolloff·파워 평탄도·저/중/고음 파워 비중을 1초 구간으로 제공합니다. 무음은 null. RMS/활성 구간 진폭 범위는 LUFS가 아니며 표본 피크는 true peak가 아닙니다. 모노 분석이라 스테레오 폭·위상·11.025 kHz 위 주파수는 다루지 않습니다. 악기·장르·감정을 자동 확정하지 않습니다.
- 타격: 주파수 대역별 onset입니다. 저음 타격을 킥, 고음 타격을 하이햇으로 확정하지 않습니다. 빌드업/드롭은 에너지와 타격 변화 후보이며 곡 구조나 의도를 보증하지 않습니다.
- 음표: Spotify Basic Pitch, 12초 창 + 1초 겹침, 최대 4,000개. 마지막 입력에는 1초의 무음 문맥을 덧붙이고 결과는 원래 분석 길이로 제한합니다(`tail-context-v1`). 혼합음에서는 반주·배음·오검출이 섞입니다. 실제 악보/보컬 멜로디가 아니며 활성도는 보정된 확률이 아닙니다.
- 코드: 다성 모드에서는 실제로 겹친 음표 조합에서 장/단3화음과 7/maj7/m7을 구분합니다. 예: C = C·E·G, Cm = C·D#·G, D7 = D·F#·A·C. 구간별 시각과 관측 음 클래스를 함께 내보냅니다. 동시에 울리지 않은 분산화음은 이 단계에서 코드로 확정하지 않습니다. 다성 음표 모델 실패 시 스펙트럼 3화음 후보만 `spectral-only`로 명시하며 구성음을 지어내지 않습니다. 독창 모드는 반주 코드를 추측하지 않으며, 조성은 선율 음의 지속 시간만으로 추정합니다.
- 조성: C# 같은 음 이름이나 C#m 같은 코드와 구분하여 `C# minor`(C# 단조)처럼 표시합니다. 음표의 지속 시간·모델 활성도를 반영한 12음 분포를 24개 장/단조 프로파일과 비교합니다. [music21의 Krumhansl-Kessler 프로파일/상관 방식](https://music21.org/music21docs/_modules/music21/analysis/discrete.html)을 참고했으며, 추가 보류 기준은 검증되지 않은 제품 휴리스틱입니다. 코드 하나·부족한 음·가까운 후보·불완전한 전사는 대표 조성을 보류합니다. 후보 상관값은 정확도 확률이 아닙니다. 전조·선법·복잡한 확장 코드·이명동음의 악보 표기는 미확인입니다.
- 가사: Whisper는 음성 전사 모델입니다. 노래·빠른 랩·다중 보컬에서 환각/누락 가능. 25초 창, 창당 생성 256토큰, 최대 2,000단어; 경계 누락 가능. 단어 타임스탬프는 음소 강제 정렬이 아닙니다. 단어 밀도와 한글 문자 음절 수는 실제 발음 밀도의 대리 지표입니다.
- 단어와 같은 시각의 음표는 반주일 수 있습니다. 가사–보컬 음표의 확정 대응, 음원 분리, 악기 분류, 자동 랩 분류는 제공하지 않습니다. 빈 결과는 무음/무보컬 판정이 아닙니다.
- 스타일·장점은 LLM이 출처와 시간 범위를 들어 해석할 영역입니다. 실제 원음을 듣지 않았다면 들었다고 말하지 않습니다. 모든 결과는 `accuracy: unvalidated`; 합성음·공개 음성 실행 검사는 실곡 정확도 검증이 아닙니다.

모델/runtime 버전: Basic Pitch 1.0.1, TFJS/WASM 3.21.0, Transformers.js 3.8.1, ONNX Runtime Web 1.22.0-dev.20250409-89f8206ba4. Whisper의 고정 리비전은 `analysis/models.mjs`, 정적 파일 SHA-256은 `public/models/manifest.json`, 라이선스는 `public/licenses/`입니다. Basic Pitch와 Whisper Base를 실제 브라우저에서 실행했으며 Small과 한국어 노래/랩의 정확도는 아직 검증하지 않았습니다.

## 개발과 검증

```powershell
npm run typecheck
npm test
npm run build
npm run db:local
npm run dev -- --host 127.0.0.1
```

`db:local`은 초기 스키마만 생성합니다. 기존 `analysis_requests` 테이블/초기 마이그레이션은 데이터 보존용으로 남지만 실행 코드에서 사용하지 않습니다. 모델 호출 경로, 키 입력과 `analyze_youtube`는 제거했습니다. 이후 테이블 제거는 별도 데이터 마이그레이션입니다.

코드·조성 검증은 `evidence/harmony/CHECKS.md`, 리듬·음색 검증은 `evidence/improve-ears/CHECKS.md`; 이전 정밀 버전은 `evidence/precision/CHECKS.md`, 과거 버전은 `evidence/keyless/`, `evidence/CHECKS.md`에 구분합니다. `tests/browser-precision.cjs`는 실제 모델/UI/MCP를, `tests/browser-lyrics.cjs`는 공개 음성으로 전사 실행을 검사합니다. `MUSIC_EVIDENCE_DIR`로 결과 폴더, `MUSIC_REAL_FIXTURE`로 이용 조건을 확인한 로컬 음악 샘플을 지정할 수 있습니다. `PLAYWRIGHT_MODULE`과 `BROWSER_EXECUTABLE`로 기존 설치를 사용합니다. `tests/precision-storage.mjs`는 큰 JSON, 용량 거부, 사용자 격리를 검사하고 생성한 ID만 정리합니다.

실제 경로: `components/music-lens.tsx` → `lib/browser-detail.ts` → 빌드한 `public/precision-worker.mjs` (소스 `analysis/precision-worker.mjs`). 리듬은 `public/precision-rhythm.mjs`, 파생 단서는 `public/analysis/musical-features.mjs`, 단어/박/음표 문맥은 `lib/alignment.ts`. 빠른 모드는 `public/audio-worker.mjs`. 공통 보고서 `lib/report.ts`의 선택적 `detail` 확장으로 이전 JSON을 보존합니다. 저장과 MCP는 `lib/server/reports.ts`, `lib/mcp.ts`입니다.

정밀 템포는 `public/analysis/tempo-grid.mjs` 하나로 교체했습니다. 음향 요약은 같은 FFT의 측정치를 `public/analysis/acoustics.mjs`에서 집계합니다. 이전 최고 자기상관 선택 함수는 제거했으며, 예전 JSON의 선택 필드가 없더라도 읽을 수 있습니다. 정밀 결과의 미확인을 과거 신호 값으로 대체하지 않습니다.

코드와 장·단조는 `public/analysis/harmony.mjs`가 음표 모델 이후 계산하며 `detail.harmony`가 정본입니다. 호환용 `rhythm.key/chords`는 같은 결과를 투영합니다. 가장 강한 3화음을 전곡 조성으로 사용하던 계산은 제거했습니다. UI의 **음높이 · 화음** 탭, JSON/Markdown, AI 팀 전달문과 MCP 요약/타임라인에서 근거를 확인합니다. 이 기능은 정밀 분석을 다시 실행하면 생성되며 과거 보고서를 몰래 다시 계산하지 않습니다.

정답 음표가 있는 실제 관악 4중주 샘플을 [examples/reference-song/README.md](examples/reference-song/README.md)에 준비했습니다. ChoraleBricks CC BY 4.0 자료이며 WAV, 개별 파트, 악보 MIDI/MusicXML, 음표 주석을 함께 보관합니다. 57초·정답 231개를 현재 브라우저 분석기로 한 번 측정한 음표 시작점/음높이 F1은 50.47%(50 ms / 50 cents), 정밀도 39.56%, 재현율 69.70%였습니다. 조건·원결과·한계는 [evidence/reference-song/CHECKS.md](evidence/reference-song/CHECKS.md)에 있습니다. 이 한 곡으로 일반적인 실곡 정확도나 개선을 주장할 수는 없으며, 가사·랩·드럼 정확도는 평가하지 않았습니다. 다음 개선은 이 기준을 보존하고 비교 조건과 예산을 먼저 고정합니다.

2026-10-14 후속 진단: 지속 중인 음의 약한 시작점 신호를 억제하는 후보를 같은 모델 출력에 비교했습니다. F1 50.47→55.17%, 미일치 음표246→189개였지만 사전 채택 기준(+5%p)에 못 미쳐 원복했습니다. 후보·오류 분류·결과는 [evidence/note-events-01/CHECKS.md](evidence/note-events-01/CHECKS.md)에 보존했습니다. 기준을 사후 변경하거나 재조정하지 않았습니다.

같은 날 두 곡으로 넓힌 세 후보도 개선 폭 또는 음표 재현율 보존 기준을 통과하지 못해 기본 디코딩을 유지했습니다. 별도로 발견한 **마지막 음 길이 잘림**은 수정했습니다. 실제 브라우저 합성음 두 개에서 끝부분 누락이 24.8→1.5ms, 97.8→0ms로 줄었고, 소수점 길이의 파일 끝을 넘어가는 반올림도 막았습니다. 일반적인 실곡 정확도 향상과는 구분합니다. [결과·실패·검증 기록](evidence/note-events-02/CHECKS.md).

키노피오P 「신 같네」 공식 배포의 보컬·하모니 참고 트랙 첫 60초도 로컬 성능 시험용으로 준비했습니다. [출처와 파일 안내](examples/pinocchiop-local/GOD-ISH.md). 앱에는 `examples/pinocchiop-local/God-ish-vocal-hamo-first60s.flac`을 넣으세요. 원본 PCM이 같은 무손실 파일이며 기존 12MiB 입력 제한 안에 들어갑니다. 이 참고 음원에는 정답 음표·가사 정렬이 없으므로 분석 실행과 정확도 채점은 구분합니다.
