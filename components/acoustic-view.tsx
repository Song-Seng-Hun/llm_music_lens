"use client";
import type { Rhythm } from "@/lib/detail";

const stamp = (n: number) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, "0")}`;
const supportLabel = { supported: "규칙적 타격과 맞는 박 후보", ambiguous: "여러 박 후보 · 확정 보류", variable: "구간마다 다른 템포 · 전체 박 확정 보류", insufficient: "박을 정할 근거 부족" };
const number = (n: number | null | undefined, digits = 1) => n == null ? "미확인" : n.toFixed(digits);

export function AcousticView({ rhythm, time, seek }: { rhythm: Rhythm; time: number; seek: (t: number) => void }) {
  const e = rhythm.tempoEvidence, a = rhythm.acoustics;
  const start = Math.floor(time / 20) * 20;
  const windows = a?.windows.filter(w => w.start >= start && w.start < start + 20) ?? [];
  const current = a?.windows.find(w => w.start <= time && w.end > time) ?? windows[0];
  return <>
    {e && <section aria-label="템포 근거">
      <h3>{supportLabel[e.status]}</h3>
      <p className="muted">박은 일정한 소리 반복을 설명하는 후보입니다. 실제 강박·박자표는 원음 확인이 필요해요.</p>
      <p>타격 포착 {Math.round(e.onsetCoverage * 100)}% · 그리드 지지 {Math.round(e.gridSupport * 100)}% · 시각 차이 중앙값 {number(e.medianOffsetMs)} ms</p>
      <p className="caption">선택한 후보와 타격 검출의 일치도이며, 정답 확률이 아닙니다. 보류된 결과에선 강박·엇박을 생성하지 않습니다.</p>
      <details className="evidence-details"><summary>구간별 템포와 판정 근거</summary>
        <p>{e.reason}</p><div className="tempo-strip">{e.segments.map((s, i) => <button key={i} onClick={() => seek(s.start)}><span>{stamp(s.start)}—{stamp(s.end)}</span>{s.bpm === null ? "미확인" : `${s.bpm.toFixed(1)} BPM`}</button>)}</div>
      </details>
    </section>}
    {a && <section aria-label="음색과 타격 밀도">
      <h3>음색 · 대역 비중 · 타격 밀도</h3>
      <p className="muted">재생 위치 {stamp(current?.start ?? time)}의 1초 측정값. 악기 이름 대신 저음의 비중, 소리의 밝기와 질감 변화를 비교할 수 있어요.</p>
      <div className="evidence-grid">
        <article>스펙트럼 중심<strong>{number(current?.centroidHz, 0)} Hz</strong><p>높을수록 고주파 쪽에 무게</p></article>
        <article>스펙트럼 평탄도<strong>{number(current?.flatness, 3)}</strong><p>0에 가까우면 집중 · 1에 가까우면 고르게 분포</p></article>
        <article>검출 타격 밀도<strong>{number(current?.onsetRate)} /초</strong><p>드럼 수·발음 수와 다름</p></article>
      </div>
      <div className="panel-label"><span>{stamp(start)}—{stamp(Math.min(start + 20, a.windows.at(-1)?.end ?? 0))} · 대역별 에너지 비중</span><span>저음 / 중음 / 고음</span></div>
      <svg className="band-chart" viewBox="0 0 800 100" role="img" aria-label="1초별 저음 중음 고음 에너지 비중">
        <rect width={800} height={100} fill="#eee9e0" />
        {windows.map(w => w.bandBalance ? <g key={w.start}><rect x={(w.start - start) * 40} y={100 * (1 - w.bandBalance.low)} width={38} height={100 * w.bandBalance.low} fill="#b64e2c" /><rect x={(w.start - start) * 40} y={100 * w.bandBalance.high} width={38} height={100 * w.bandBalance.mid} fill="#697d56" /><rect x={(w.start - start) * 40} y={0} width={38} height={100 * w.bandBalance.high} fill="#7975a0" /><title>{stamp(w.start)} 저음 {Math.round(w.bandBalance.low * 100)}% · 중음 {Math.round(w.bandBalance.mid * 100)}% · 고음 {Math.round(w.bandBalance.high * 100)}%</title></g> : null)}
      </svg>
      <div className="tempo-strip">{windows.map(w => <button key={w.start} onClick={() => seek(w.start)}><span>{stamp(w.start)}</span>{w.rmsDbfs === null ? "무음" : `${w.rmsDbfs.toFixed(1)} dBFS`}</button>)}</div>
      <p className="caption">저음 &lt;180 Hz · 중음 180–2,000 Hz · 고음 ≥2,000 Hz. 회색은 근거 없음. RMS는 청감 음량(LUFS)이 아닙니다.</p>
      <details className="evidence-details"><summary>전체 진폭과 측정 한계</summary>
        <p>표본 피크 {number(a.summary.samplePeakDbfs)} dBFS · 활성 1초 구간 P90−P10 {number(a.summary.activeWindowDynamicRangeDb)} dB · 풀스케일 근접 표본 {number(a.summary.nearFullScaleFraction * 100, 3)}%</p>
        <p>{a.method}</p><ul>{a.warnings.map(w => <li key={w}>{w}</li>)}</ul>
      </details>
    </section>}
  </>;
}
