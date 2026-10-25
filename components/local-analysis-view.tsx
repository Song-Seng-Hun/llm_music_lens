"use client";
import { localSummary, localTimeline, type LocalAnalysis } from "@/lib/local-analysis";

const stateLabels: Record<string, string> = { complete: "완료", partial: "일부 결과", failed: "실패", unavailable: "사용 불가", not_requested: "요청 안 함" };
export function LocalAnalysisView({ local, time, seek }: { local: LocalAnalysis; time: number; seek: (n: number) => void }) {
  const duration = local.audio.duration_seconds;
  const start = Math.min(Math.floor(Math.max(0, time) / 30) * 30, Math.max(0, Math.ceil(duration / 30) * 30 - 30));
  const end = Math.min(duration, start + 30), summary = localSummary(local)!;
  const page = localTimeline(local, start, end), e = local.emotion;
  const mode = e.mode === "voice" ? "말소리 감정" : e.mode === "music" ? "음악 분위기" : "선택하지 않음";
  return <div aria-label="로컬 단어·감정 분석">
    <div className="section-heading"><h3>단어와 감정 단서</h3><span>{stateLabels[local.state]}</span></div>
    <p>Whisper: {stateLabels[local.asr_status]} · {local.word_count}단어 / {mode}: {stateLabels[e.status]} · {e.windows.length}구간</p>
    <div className="note-box"><p>분류 점수는 감정의 양이나 보정된 확률이 아닙니다. 고조도는 {e.mode === "voice" ? "대략 0–1" : "대략 1–9"}인 모델 고유 척도이며 두 모드끼리 비교하지 않습니다. 노래의 말소리 감정과 음악 분위기 정확도는 미검증입니다.</p>
      <p>분석 창이 겹치며 구간 전체의 추정값입니다. 순간의 확정 감정이 아닙니다. 감정 미관측: {e.coverage.uncovered_seconds.toFixed(3)}초. 빈 구간은 중립이나 무음을 뜻하지 않습니다.</p>
      {summary.emotion.highest_observed_arousal && <p>관측한 창 중 가장 높은 고조도: <button className="time-link" onClick={() => seek(summary.emotion.highest_observed_arousal!.start)}>{summary.emotion.highest_observed_arousal.start.toFixed(3)}–{summary.emotion.highest_observed_arousal.end.toFixed(3)}초</button> · {summary.emotion.highest_observed_arousal.arousal.toFixed(3)} · {summary.emotion.highest_observed_arousal.label}</p>}
    </div>
    {!!local.warnings.length && <ul aria-label="분석 경고">{local.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
    {!!e.warnings.length && <ul aria-label="감정 분석 경고">{e.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
    <p className="caption">가져온 모델 추정입니다. 원음·모델 실행 여부를 서버가 독립 검증한 것은 아닙니다. 원음 파일은 결과 JSON에 포함되지 않습니다.</p>
    <div className="section-heading"><h4>{start.toFixed(3)}–{end.toFixed(3)}초</h4><span>원본 시간축</span></div>
    <div><button className="secondary" disabled={start === 0} onClick={() => seek(Math.max(0, start - 30))}>이전 구간</button> <button className="secondary" disabled={end >= duration} onClick={() => seek(end)}>다음 구간</button></div>
    <h4>단어별 시작·종료</h4>
    {!page.localWords.items.length ? <p>이 구간에 보존된 단어가 없습니다. 전사 실패·미인식 여부를 상태에서 확인하세요.</p> : <div className="section-list">{page.localWords.items.map((row, i) => <div className="section-row" key={i}>
      <span><button className="time-link" onClick={() => seek(row.start)}>{row.start.toFixed(3)}</button>–<button className="time-link" onClick={() => seek(Math.min(row.end, duration))}>{row.end.toFixed(3)}</button>초</span>
      <span><strong>{row.text}</strong><small>원본 단어 점수: {row.probability === undefined ? "미제공" : row.probability.toFixed(3)} · 비보정</small></span>
    </div>)}</div>}
    {page.localWords.truncated && <p>일치 단어 {page.localWords.total}개 중 200개 표시. 전체 JSON 또는 더 짧은 MCP 구간으로 조회하세요.</p>}
    {!!local.timing_notes.length && <p className="caption">원본 종료 시간이 음원 끝을 최대 {local.timing_notes[0].max_overshoot_seconds.toFixed(3)}초 넘는 {local.timing_notes[0].word_count}단어를 수정 없이 보존했습니다. 재생 버튼만 음원 끝까지 이동합니다.</p>}
    <h4>{mode} · 고조도</h4>
    {!page.localEmotion.items.length ? <p>이 구간에 감정 창이 없습니다. 요청 안 함·실패·미관측 구간을 확인하세요.</p> : <div className="moment-list">{page.localEmotion.items.map((row, i) => {
      const labels = "category_labels" in row ? row.category_labels : row.mood_labels;
      const scores = "category_scores" in row ? row.category_scores : row.mood_scores;
      const label = "dominant_emotion" in row ? row.dominant_emotion : row.top_mood;
      return <article className="moment" key={i}><div><button className="time-link" onClick={() => seek(row.start)}>{row.start.toFixed(3)}</button>–<button className="time-link" onClick={() => seek(row.end)}>{row.end.toFixed(3)}</button>초</div><div>
        <h4>{label}</h4><p>고조도 {row.arousal.toFixed(3)} · 긍정성 {row.valence.toFixed(3)}{"dominance" in row ? ` · 지배성 ${row.dominance.toFixed(3)}` : ""}</p>
        <details><summary>전체 범주 점수 · 비보정</summary><ul>{labels.map((name, index) => <li key={name}>{name}: {scores[index].toFixed(6)}</li>)}</ul></details>
      </div></article>;
    })}</div>}
    {page.localEmotion.truncated && <p>일치 감정 창 {page.localEmotion.total}개 중 200개 표시.</p>}
  </div>;
}
