"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowDownToLine, ArrowRight, AudioLines, Check, CircleHelp, FileAudio, FolderOpen, Headphones, Link2, LoaderCircle, Plus, Upload, X } from "lucide-react";
import { asMarkdown, MAX_FILE_BYTES, MAX_REPORT_BYTES, reportSchema, SCHEMA_VERSION, type MusicReport } from "@/lib/report";
import { parseYouTube } from "@/lib/validation";
import { audioMime, decodeFile, downloadText, fingerprint, runSignalWorker } from "@/lib/browser-audio";
import { detailSignal, resampleSamples, runDetailWorker, speechSamples, type DetailOptions } from "@/lib/browser-detail";
import { demoAudio } from "@/lib/demo-audio";
import { asTeamHandoff } from "@/lib/handoff";
import { ReportView } from "./report-view";
type Status = { user: { name: string } | null; storageReady: boolean; localPreview: boolean };
type Saved = { id: string; title: string; source_type: string; created_at: string };
const EXAMPLE = "https://www.youtube.com/watch?v=ALxVpirKqRQ";
async function responseJson<T = { report: unknown }>(response: Response): Promise<T> { const body = await response.json(); if (!response.ok) { const message = body && typeof body === "object" && "error" in body ? (body.error as { message?: string })?.message : undefined; throw new Error(message ?? "요청을 완료하지 못했습니다."); } return body as T; }

export default function MusicLens() {
  const [mode, setMode] = useState<"file" | "youtube">("file"), [url, setUrl] = useState(""), [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null), [audioUrl, setAudioUrl] = useState("");
  const [report, setReport] = useState<MusicReport | null>(null), [status, setStatus] = useState<Status | null>(null);
  const [saved, setSaved] = useState<Saved[]>([]), [libraryError, setLibraryError] = useState("");
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<"local" | "save" | null>(null), [progress, setProgress] = useState(0);
  const [analysisMode, setAnalysisMode] = useState<"quick" | "detail">("detail"), [lyrics, setLyrics] = useState<DetailOptions["lyrics"]>("off"), [language, setLanguage] = useState("korean"), [stageLabel, setStageLabel] = useState("");
  const [noteMode, setNoteMode] = useState<NonNullable<DetailOptions["notes"]>>("polyphonic");
  const [time, setTime] = useState(0), [videoStart, setVideoStart] = useState<number | null>(null);
  const [reference, setReference] = useState<{ id: string; url: string } | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false), [dragging, setDragging] = useState(false);
  const audio = useRef<HTMLAudioElement>(null), fileInput = useRef<HTMLInputElement>(null), importInput = useRef<HTMLInputElement>(null);
  const attachInput = useRef<HTMLInputElement>(null);
  const currentReportId = useRef<string | undefined>(undefined);
  useEffect(() => { currentReportId.current = report?.id; }, [report?.id]);
  const lifecycle = useRef<{ generation: number; cancel: (() => void) | null; url: string }>({ generation: 0, cancel: null, url: "" });

  async function refreshLibrary() { try { const result = await responseJson<{ reports: Saved[] }>(await fetch("/api/reports")); setSaved(result.reports); setLibraryError(""); } catch (e) { setLibraryError((e as Error).message); } }
  useEffect(() => {
    fetch("/api/status").then(r => responseJson<Status>(r)).then(s => { setStatus(s); if (s.user) void refreshLibrary(); }).catch(() => setError("서버 상태를 읽지 못했습니다. 로컬 파일 분석은 계속 사용할 수 있어요."));
    const task = lifecycle.current;
    return () => { task.generation++; task.cancel?.(); if (task.url) URL.revokeObjectURL(task.url); };
  }, []);
  function setAudioFile(next: File | null) { if (lifecycle.current.url) URL.revokeObjectURL(lifecycle.current.url); const url = next ? URL.createObjectURL(next) : ""; lifecycle.current.url = url; setFile(next); setAudioUrl(url); }

  function chooseFile(next?: File) {
    if (!next || busy) return; setError(""); setNotice("");
    try { audioMime(next); if (next.size > MAX_FILE_BYTES || !next.size) throw new Error("12 MiB 이하의 음원 파일을 선택해 주세요."); } catch (e) { setError((e as Error).message); return; }
    setMode("file"); setAudioFile(next); setTitle(next.name.replace(/\.[^.]+$/, "").slice(0, 300)); setReport(null); setTime(0); setVideoStart(null);
  }
  function reset() { if (busy) return; setReport(null); setAudioFile(null); setTitle(""); setUrl(""); setReference(null); setMode("file"); setTime(0); setVideoStart(null); setError(""); setNotice(""); }
  async function localAnalysis() {
    if (!file || busy) return; const run = ++lifecycle.current.generation; setBusy("local"); setError(""); setNotice(""); setProgress(0.02);
    try {
      setStageLabel("음원 읽는 중");
      const solo = analysisMode === "detail" && noteMode === "solo-vocal";
      const decoded = await decodeFile(file, analysisMode === "detail" ? solo ? 44100 : 22050 : 16000); if (run !== lifecycle.current.generation) return;
      let signal, detail;
      if (analysisMode === "detail") {
        const samples = solo ? await resampleSamples(decoded.samples, decoded.sampleRate, 22050) : decoded.samples; if (run !== lifecycle.current.generation) return;
        const voice = lyrics === "off" ? null : await speechSamples(decoded.samples, decoded.sampleRate); if (run !== lifecycle.current.generation) return;
        const task = runDetailWorker(samples, voice, { lyrics, language, notes: noteMode }, (p, label) => { if (run === lifecycle.current.generation) { setProgress(p); setStageLabel(label); } }, solo ? decoded.samples : null);
        lifecycle.current.cancel = task.cancel; detail = await task.promise; signal = detailSignal(samples, detail);
      } else {
        setStageLabel("파형 · 리듬 · 음높이 계산 중");
        const task = runSignalWorker(decoded.samples, decoded.sampleRate, p => { if (run === lifecycle.current.generation) setProgress(Math.max(0.04, p)); }); lifecycle.current.cancel = task.cancel;
        signal = await task.promise;
      }
      if (run !== lifecycle.current.generation) return;
      const result = reportSchema.parse({ schema: SCHEMA_VERSION, id: crypto.randomUUID(), createdAt: new Date().toISOString(), source: { kind: "file", title: title.trim() || file.name, sha256: decoded.sha256, duration: decoded.duration }, signal, ...(detail ? { detail } : {}), interpretation: null, provenance: { mode: "local-signal", ...(detail ? { model: detail.notes.model } : {}), inputStatus: "browser-decoded; original SHA-256 computed locally", accuracy: "unvalidated" } });
      setReport(result);
      setNotice(detail && (detail.notes.status === "failed" || detail.lyrics.status === "failed") ? "일부 모델이 실패했습니다. 완료된 분석은 보존했으며 각 탭에서 실패 이유를 확인할 수 있어요." : "브라우저 분석 완료. 음원은 전송되지 않았습니다. 추정 결과를 원음과 대조해 주세요.");
    } catch (e) { if (run === lifecycle.current.generation && (e as Error).name !== "AbortError") setError((e as Error).message); }
    finally { if (run === lifecycle.current.generation) { setBusy(null); lifecycle.current.cancel = null; } }
  }
  function cancel() { lifecycle.current.generation++; lifecycle.current.cancel?.(); lifecycle.current.cancel = null; setBusy(null); setNotice("로컬 분석을 취소했습니다."); }
  function checkReference() {
    try { setReference(parseYouTube(url)); setError(""); }
    catch (e) { setReference(null); setError((e as Error).message); }
  }
  async function save() {
    if (!report || busy) return; setBusy("save"); setError("");
    try { const result = await responseJson(await fetch("/api/reports", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(report) })); setReport(reportSchema.parse(result.report)); setNotice("현재 결과를 저장했습니다. 브라우저 제공 데이터라는 출처를 함께 기록했습니다."); await refreshLibrary(); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }
  async function openSaved(id: string) { if (busy) return; try { const result = await responseJson(await fetch(`/api/reports/${encodeURIComponent(id)}`)); const parsed = reportSchema.parse(result.report); setReport(parsed); setAudioFile(null); setTime(0); setVideoStart(null); setError(""); setNotice("저장된 분석을 열었습니다. 원음 파일은 저장하지 않습니다."); } catch (e) { setError((e as Error).message); } }
  async function importReport(next?: File) { if (!next || busy) return; try { if (next.size > MAX_REPORT_BYTES) throw new Error("분석 JSON은 1.9 MB 이하여야 합니다."); const parsed = reportSchema.safeParse(JSON.parse(await next.text())); if (!parsed.success) throw new Error("지원하는 Music Lens JSON이 아닙니다."); setReport({ ...parsed.data, provenance: { ...parsed.data.provenance, mode: "imported", inputStatus: "imported JSON; not independently validated" } }); setAudioFile(null); setVideoStart(null); setTime(0); setError(""); setNotice("JSON을 불러왔습니다. 가져온 내용의 정확도는 별도로 확인해야 합니다."); } catch (e) { setError(e instanceof SyntaxError ? "JSON을 읽지 못했습니다." : (e as Error).message); } }
  function seek(seconds: number) { setTime(seconds); if (audio.current) { audio.current.currentTime = seconds; void audio.current.play().catch(() => undefined); } else if (report?.source.youtubeId) setVideoStart(Math.floor(seconds)); }
  async function attachAudio(next?: File) {
    if (!next || !report || busy) return;
    const expected = report.id;
    try { audioMime(next); if (!next.size || next.size > MAX_FILE_BYTES) throw new Error("12 MiB 이하 파일을 선택해 주세요."); if (!report.source.sha256 || await fingerprint(await next.arrayBuffer()) !== report.source.sha256) throw new Error("분석한 원본과 같은 파일인지 확인할 수 없습니다. 같은 파일을 선택하세요."); if (expected !== currentReportId.current) return; setAudioFile(next); setTime(0); setNotice("원본 해시가 일치합니다. 오디오를 연결했습니다."); } catch (e) { setError((e as Error).message); }
  }

  return <div className="app-shell">
    <aside className="sidebar"><Link className="brand" href="/" aria-label="Music Lens 홈"><span className="brand-icon"><AudioLines size={23} /></span><span>music lens<span className="brand-dot">.</span></span></Link><p className="sidebar-caption">A CLOSER LISTEN</p><button className="new-button" onClick={reset} disabled={!!busy}><Plus size={17} /> 새로운 분석</button><div className="sidebar-label">YOUR LIBRARY <span>{saved.length || ""}</span></div><nav className="library" aria-label="저장된 분석">{saved.map(item => <button key={item.id} className={report?.id === item.id ? "active" : ""} onClick={() => openSaved(item.id)} disabled={!!busy}><span className="library-icon">{item.source_type === "youtube" ? <Link2 size={16} /> : <FileAudio size={16} />}</span><span><strong>{item.title}</strong><small>{new Date(item.created_at).toLocaleDateString("ko-KR")}</small></span></button>)}</nav>{!saved.length && <p className="library-empty">{status?.user ? "첫 분석을 저장해 보세요." : "로그인하면 저장한 분석이 여기에 모입니다."}</p>}{libraryError && <p className="library-error">{libraryError}</p>}<button className="sidebar-action" onClick={() => importInput.current?.click()} disabled={!!busy}><FolderOpen size={16} /> 분석 JSON 불러오기</button><div className="sidebar-bottom"><button className="sidebar-action" onClick={() => setToolsOpen(true)}><AudioLines size={16} /> AI 팀에서 사용하기</button><div className="small-print">Hear the details.<br />Keep the evidence.</div></div></aside>
    <main className="workspace"><header className="topbar"><span>WORKSPACE <span className="slash">/</span> 음악 분석실</span><div><span className="local-status"><i /> 로컬 분석 사용 가능</span>{status?.user ? <span className="user-pill" title={status.user.name}>{status.localPreview ? "로컬 · " : ""}{status.user.name.includes("@") ? status.user.name.split("@")[0] : status.user.name}</span> : <a className="login" href="/signin-with-chatgpt?return_to=/" target="_top">{status?.localPreview ? "로컬 로그인" : "로그인"} <ArrowRight size={14} /></a>}</div></header>
      <div className="workspace-body"><div className="hero"><div className="eyebrow">YOUR TEAM’S LISTENING ROOM <span>01 / INPUT</span></div><h1>좋은 음악의 <em>이유</em>를 듣다.</h1><p>음원은 내 PC에서, 해석은 우리 AI 팀과.<br className="mobile-break" /> 박자와 음높이의 단서를 먼저 살펴보세요.</p></div>
        <section className="source-panel" aria-label="음원 선택"><div className="source-tabs"><button className={mode === "file" ? "selected" : ""} onClick={() => { if (!busy) setMode("file"); }} disabled={!!busy}><Upload size={16} /> 음원 파일</button><button className={mode === "youtube" ? "selected" : ""} onClick={() => { if (!busy) setMode("youtube"); }} disabled={!!busy}><Link2 size={16} /> YouTube 링크</button><span>최대 10분</span></div>
          {mode === "file" ? <div className={`dropzone ${dragging ? "dragging" : ""}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); chooseFile(e.dataTransfer.files[0]); }}><button className="drop-target" onClick={() => fileInput.current?.click()} disabled={!!busy}><span className="upload-icon">{file ? <FileAudio size={25} /> : <AudioLines size={27} />}</span><strong>{file ? file.name : "분석할 음악을 여기 놓아 주세요"}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MiB · 다른 파일 선택` : "또는 클릭하여 파일 선택"}</span><small>MP3, WAV, FLAC, M4A 외 · 최대 12 MiB</small></button><div className="privacy-note"><span className="tiny-dot" /> API 키 없이 브라우저에서 분석 · 음원 전송 없음</div></div> : <div className="youtube-input"><label htmlFor="youtube-url">공개 영상 주소</label><div className="url-field"><Link2 size={18} /><input id="youtube-url" value={url} onChange={e => { setUrl(e.target.value); setReference(null); }} disabled={!!busy} placeholder="https://www.youtube.com/watch?v=..." type="url" /></div><button className="text-button" onClick={() => { setUrl(EXAMPLE); setReference(null); }} disabled={!!busy}>보내주신 예시 곡 넣기 ↗</button><p>참고 영상 주소만 확인합니다. 주소만으로는 음원을 분석하지 않습니다. BPM·음높이는 파일을 넣어 분석해 주세요.</p></div>}
          {mode === "file" && <div className="analysis-settings"><div className="analysis-mode"><label>분석 깊이<select aria-label="분석 깊이" value={analysisMode} onChange={e => setAnalysisMode(e.target.value as "quick" | "detail")} disabled={!!busy}><option value="detail">정밀 · 리듬 / 화음 / 음표 모델</option><option value="quick">빠른 신호 분석</option></select></label>{analysisMode === "detail" && <><label>음표 대상<select aria-label="음표 대상" value={noteMode} onChange={e => setNoteMode(e.target.value as NonNullable<DetailOptions["notes"]>)} disabled={!!busy}><option value="polyphonic">혼합음 · 다성 (Basic Pitch)</option><option value="solo-vocal">독창 · 보컬만 (SOME)</option></select></label><label>단어 전사 (선택)<select aria-label="단어 전사" value={lyrics} onChange={e => setLyrics(e.target.value as DetailOptions["lyrics"])} disabled={!!busy}><option value="off">끄기</option><option value="base">Whisper Base · 가벼운 초안</option><option value="small">Whisper Small · 더 큰 모델</option></select></label>{lyrics !== "off" && <label>가사 언어<select aria-label="가사 언어" value={language} onChange={e => setLanguage(e.target.value)} disabled={!!busy}><option value="korean">한국어</option><option value="english">영어</option><option value="japanese">일본어</option><option value="auto">자동 (추정)</option></select></label>}</>}</div><p className="caption">{analysisMode === "detail" ? "음표 모델은 이 사이트에서, 가사 모델은 Hugging Face에서 첫 실행 시 내려받습니다. 가사는 수백 MB급 다운로드와 긴 계산이 필요할 수 있어요. API 키·음원 업로드 없음. 컴퓨터 성능에 따라 수 분 이상 걸립니다." : "가벼운 파형·BPM·희소 음높이 추정. 모델을 내려받지 않습니다."}</p>{analysisMode === "detail" && noteMode === "solo-vocal" && <p className="caption">SOME은 반주 없이 한 사람만 노래하는 음원이 필요합니다. 반주가 있는 곡은 보컬을 따로 준비해 주세요. 이 사이트는 보컬을 분리하지 않습니다. 약 450 MiB 모델을 내려받아 브라우저에서 처리합니다. 모델 라이선스: CC BY-NC-SA 4.0. 음정은 소수 MIDI로 보존하고 모델이 제공하지 않는 신뢰도는 미제공으로 표시합니다.</p>}<button className="text-button" disabled={!!busy} onClick={() => chooseFile(demoAudio())}>파일 없이 합성 데모로 체험 (16초 · 120 BPM)</button></div>}
          {mode === "file" && file && <div className="input-footer"><label htmlFor="track-title">분석 이름<input id="track-title" value={title} maxLength={300} onChange={e => setTitle(e.target.value)} disabled={!!busy} placeholder="곡 이름 또는 작업 이름" /></label><button className="primary" disabled={!!busy} onClick={localAnalysis}>{busy === "local" ? <LoaderCircle className="spin" size={17} /> : <AudioLines size={17} />}{busy === "local" ? "분석 중" : analysisMode === "detail" ? "정밀 분석 시작" : "파일 분석"}<ArrowRight size={16} /></button></div>}
          {mode === "youtube" && <div className="reference-actions"><button className="secondary" disabled={!!busy || !url.trim()} onClick={checkReference}>참고 주소 확인 <ArrowRight size={16} /></button>{reference && <p className="note-box">주소 형식 확인됨 · 음원 미분석<br /><a href={reference.url} target="_blank" rel="noreferrer">YouTube에서 영상 열기 ↗</a></p>}</div>}</section>
        {busy === "local" && <div className="progress-box" role="status"><div><span>{stageLabel}</span><button onClick={cancel}>취소</button></div><progress max="1" value={progress} /></div>}{error && <div className="message error" role="alert"><CircleHelp size={18} /><span>{error}</span><button aria-label="오류 닫기" onClick={() => setError("")}><X size={16} /></button></div>}{notice && <div className="message success" role="status"><Check size={17} /><span>{notice}</span></div>}
        {report ? <><div className="report-actions"><div><button className="secondary" onClick={() => downloadText(asTeamHandoff(report), `music-lens-${report.id}-handoff.md`, "text/markdown")}><ArrowDownToLine size={16} /> AI 팀 전달문</button></div><div><button onClick={save} disabled={!!busy || !status?.user}><FolderOpen size={15} /> 결과 저장</button><button onClick={() => downloadText(JSON.stringify(report), `music-lens-${report.id}.json`, "application/json")}><ArrowDownToLine size={15} /> JSON</button><button onClick={() => downloadText(asMarkdown(report), `music-lens-${report.id}.md`, "text/markdown")}>Markdown</button></div></div><p className="caption">전달문과 JSON을 기존 AI 대화에 첨부하세요. 음표·단어 타이밍은 추정입니다. AI가 스타일·랩의 장점을 해석하려면 원음이나 직접 확인한 근거가 필요해요.</p>{!audioUrl && report.source.sha256 && <button className="secondary" disabled={!!busy} onClick={() => attachInput.current?.click()}>같은 원음 연결 · 구간 확인</button>}{audioUrl && <audio ref={audio} src={audioUrl} controls onTimeUpdate={e => setTime(e.currentTarget.currentTime)} className="audio-player" />}{report.source.youtubeId && <div className="video-area">{videoStart !== null ? <iframe key={videoStart} title="분석 원본 YouTube 영상" src={`https://www.youtube-nocookie.com/embed/${report.source.youtubeId}?start=${videoStart}&autoplay=1`} allow="autoplay; encrypted-media" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" /> : <button className="video-load" onClick={() => setVideoStart(0)}><Headphones size={19} /> 원본 영상 플레이어 열기 <span>YouTube에 연결</span></button>}</div>}<ReportView report={report} time={time} seek={seek} /></> : <section className="listening-preview" aria-label="분석 안내"><div className="record-visual" aria-hidden="true"><div className="record-disc"><div className="record-label"><AudioLines size={26} /></div></div><span className="needle" /></div><div><div className="eyebrow">LESS GUESSING. MORE LISTENING.</div><h2>막연한 감상을,<br />다시 들을 수 있는 근거로.</h2><p>‘후렴이 좋다’에서 멈추지 않도록.<br />로컬 수치와 직접 들은 근거를 함께 살펴보세요.</p><div className="feature-index"><span><b>01</b> 박자와 음높이</span><span><b>02</b> 시간축 확인</span><span><b>03</b> AI 팀에 전달</span></div></div></section>}
        <footer className="page-footer"><span>MUSIC LENS / WORKING EDITION</span><span>추정과 해석을 구분합니다. 모델 실행과 실제 곡의 정확도 검증은 구분합니다.</span></footer>
      </div></main>
    <input ref={attachInput} type="file" accept="audio/*" hidden onChange={e => { void attachAudio(e.target.files?.[0]); e.target.value = ""; }} /><input ref={fileInput} data-testid="audio-file" type="file" accept="audio/*,.mp3,.wav,.flac,.ogg,.m4a,.aac,.webm" hidden onChange={e => { chooseFile(e.target.files?.[0]); e.target.value = ""; }} /><input ref={importInput} type="file" accept="application/json,.json" hidden onChange={e => { void importReport(e.target.files?.[0]); e.target.value = ""; }} />
    {toolsOpen && <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) setToolsOpen(false); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><button className="modal-close" aria-label="닫기" onClick={() => setToolsOpen(false)}><X size={20} /></button><span className="eyebrow">FOR YOUR AI TEAM</span><h2 id="dialog-title">같은 음악, 같은 근거.</h2><p>파일 분석 후 ‘AI 팀 전달문’과 JSON을 내려받아 기존 ChatGPT·Claude·Gemini 대화에 첨부하세요. 선택한 분석 모델은 브라우저에서 실행하며, 클라우드 추론 API는 사용하지 않습니다.</p><p>스타일·가사·랩 해석에는 원음에 접근할 수 있는 AI나 직접 들은 기록이 필요합니다. 신호 수치만으로 들었다고 주장하지 않도록 전달문에 한계를 포함합니다.</p><div className="tool-list"><code>list_music_reports</code><span>저장된 분석 찾기</span><code>get_music_report</code><span>근거·한계 읽기</span><code>get_music_timeline</code><span>최대 120초 구간 조회</span></div><p className="note-box">MCP 연결 주소: 게시된 사이트의 <code>/mcp</code><br />도구 3개는 결과 조회 전용입니다. Sites 배포·팀 연결은 아직 미검증이며, 지금은 파일로 전달합니다.</p><button className="primary" onClick={() => setToolsOpen(false)}>확인 <ArrowRight size={16} /></button></section></div>}
  </div>;
}
