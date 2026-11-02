"use client";
import type { Harmony } from "@/lib/detail";

export const formatKeyLabel = (value: string | null) => value?.replace(/ major$/, " 장조").replace(/ minor$/, " 단조") ?? "미확인";
const stamp = (v: number) => `${Math.floor(v / 60)}:${(v % 60).toFixed(2).padStart(5, "0")}`;
const states = { estimated: "조성 후보 있음 · 정확도 미검증", ambiguous: "후보를 구분하기 어려움", insufficient: "조성을 판단할 음표 근거 부족", unavailable: "음표 분석을 사용할 수 없음" };

export function HarmonyView({ harmony: h, start, end, seek, solo = false }: { harmony: Harmony; start: number; end: number; seek: (t: number) => void; solo?: boolean }) {
  const chords = h.chords.filter(c => c.start < end && c.end > start);
  return <section aria-label="코드와 장단조" className="note-box">
    <h3>코드와 장·단조</h3>
    <p className="muted">C♯는 음 이름, C♯m은 코드, C♯ 단조는 곡의 조성입니다. 같은 음들을 써도 중심음에 따라 장·단조가 달라질 수 있어요.</p>
    <h4 data-testid="harmony-key">{formatKeyLabel(h.key.label)} <small>· {states[h.key.status]}</small></h4>
    {h.key.candidates.length > 0 && <><p>비교 후보: {h.key.candidates.map(k => `${formatKeyLabel(k.label)} (${k.correlation.toFixed(3)})`).join(" · ")}</p><p className="caption">괄호는 음 분포와 장·단조 패턴의 상관값입니다. 정확도 확률이 아니며, 음 몇 개나 코드 하나만으로 조성을 확정하지 않습니다.</p></>}
    {!!h.pitchClasses.length && <p className="muted">{solo ? "선율 음 길이를 반영한 비중 (모델 신뢰도 미제공)" : "음 길이·모델 활성도를 반영한 비중"}: {h.pitchClasses.map(p => `${p.name} ${Math.round(p.share * 100)}%`).join(" · ")}</p>}
    <h4>구간별 코드 후보</h4>
    <p className="caption">{solo ? "독창 모드는 반주 코드를 판정하지 않습니다." : h.source === "note-events" ? "동시에 잡힌 음표 조합 기준 · 장/단3화음, 7, maj7, m7" : "음표 모델을 사용할 수 없어 스펙트럼 후보만 표시 · 구성음 미확인"} · {stamp(start)}—{stamp(end)}</p>
    <div className="chord-list">{chords.map((c, i) => <button key={i} onClick={() => seek(c.start)}><strong>{c.label}</strong><small>{stamp(c.start)}—{stamp(c.end)}</small><span>{c.pitchClasses.join(" · ") || "구성음 미확인"}</span></button>)}</div>
    {!chords.length && <p className="muted">이 구간에서 조건을 충족하는 코드를 찾지 못했습니다. 단선율이나 빠른 분산화음은 코드로 확정하지 않습니다.</p>}
    <p className="caption">혼합 음원에서 잘못 잡힌 음표가 결과에 영향을 줍니다. 전조·선법·복잡한 확장 코드·이명동음 표기는 미확인입니다.{h.truncated ? " 결과 일부가 한도로 생략되었습니다." : ""}</p>
    <details className="evidence-details"><summary>추정 방법과 한계</summary><p>{h.method}</p><p>{h.key.reason}</p><ul>{h.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details>
  </section>;
}
