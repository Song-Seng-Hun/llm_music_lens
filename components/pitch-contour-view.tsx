import { contourInterval, type PitchContour } from "@/lib/pitch-contour";

export function PitchContourView({ contour, start, end }: { contour?: PitchContour; start: number; end: number }) {
  if (!contour) return <p className="muted">연속 음높이 자료 없음 · 새 독창 분석에서 생성됩니다. 미검출·무음 판정이 아닙니다.</p>;
  const page = contourInterval(contour, start, end, 2000);
  const voiced = page.items.filter((p): p is { time: number; midi: number } => p.midi !== null);
  const low = voiced.length ? Math.max(0, Math.floor(Math.min(...voiced.map(p => p.midi))) - 1) : 48;
  const high = voiced.length ? Math.min(127, Math.max(low + 2, Math.ceil(Math.max(...voiced.map(p => p.midi))) + 1)) : 72;
  let path = "", previous: number | null = null;
  for (const p of page.items) {
    if (p.midi === null) { previous = null; continue; }
    const x = 45 + (p.time - start) / (end - start) * 750, y = 200 - (p.midi - low) / (high - low) * 180;
    path += `${previous === null || p.time - previous > contour.stepSeconds * 1.5 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)} `;
    previous = p.time;
  }
  const label = (m: number) => `${["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"][m % 12]}${Math.floor(m / 12) - 1}`;
  return <section aria-label="연속 음높이 곡선">
    <h3>연속 음높이 곡선</h3>
    <p className="muted">약 {(contour.stepSeconds * 1000).toFixed(2)}ms 간격의 보컬 음높이 추정 · 현재 {start.toFixed(0)}–{end.toFixed(0)}초. 음표 안의 흔들림·이동을 살펴볼 자료이며, 비브라토·슬라이드의 자동 판정은 아닙니다.</p>
    <svg className="detail-roll" viewBox="0 0 800 220" role="img" aria-label="현재 구간의 연속 보컬 음높이 추정">
      <rect width={800} height={220} fill="#eee9e0" />
      {[low, Math.round((low + high) / 2), high].map(n => <g key={n}><line x1={45} x2={800} y1={200 - (n - low) / (high - low) * 180} y2={200 - (n - low) / (high - low) * 180} stroke="#d7d0c5" /><text x={2} y={200 - (n - low) / (high - low) * 180} fontSize={11}>{label(n)}</text></g>)}
      <path d={path} fill="none" stroke="#376e7c" strokeWidth={1.5} />
    </svg>
    <p className="caption">{page.items.length}개 프레임 · 빈 구간은 모델의 무성 추정 또는 자료 없음이며 실제 무음을 뜻하지 않습니다. 구간 경계는 추정 차이가 있을 수 있습니다. {contour.status === "partial" || page.truncated ? "일부 결과: 전체 범위가 담기지 않았습니다." : "JSON에 원래 시간 간격과 음높이를 보존합니다."}</p>
  </section>;
}
