# Music Lens architecture

Current requirement: no personal model API key registered on Sites. This is a deployment/credential preference, not a claim that all APIs or paid services are prohibited.

Actual flow: local file -> browser AudioContext decode -> 16 kHz mono analysis buffer -> cancellable worker -> music-lens/1 report -> UI/playback -> exported JSON/Markdown/team handoff or explicit owner-scoped D1 save. Audio bytes stay in the browser. No model runtime, key input, provider upload, or model-calling tool remains.

ChatGPT/other supported agents consume saved evidence using the Site MCP tools and interpret it in their own conversation. Sites owns the hosted identity/OAuth connection. This is not an internal-model invocation API. The supplied official Sites reference documents this direction; hosted publication and actual caller identity are not verified yet.

Entrypoints: app/page.tsx -> components/music-lens.tsx + report-view.tsx -> lib/browser-audio.ts -> public/audio-worker.mjs. lib/handoff.ts builds a bounded packet with disclosed sampling; the full report JSON retains detail. app/api/reports and app/mcp -> lib/server/reports.ts + shared lib/report.ts/lib/mcp.ts. app/api/status reports local mode and storage availability.

Compatibility: retain existing report interpretations/model provenance for import/read only, and the inert analysis_requests table/initial migration to preserve storage. No request handler uses that table. Removal requires a separately scoped migration; no parallel provider implementation is retained.

Limits: 12 MiB files, 600 seconds; sparse pitch and heuristic tempo only. No precise lyrics, chord recognition, stems, rap alignment, calibrated accuracy or URL-only analysis. YouTube input is a reference link, not media ingestion. UI explicitly labels missing evidence. Manual exports work before Sites deployment.

Validation scope and original failures are recorded in TASKS.md and evidence/keyless/CHECKS.md. Earlier evidence/CHECKS.md and REVIEW.md refer to the pre-correction provider implementation, not the current one. Four obsolete provider tests were removed with that executable contract; URL, byte/origin and MCP boundaries remain, with new handoff/legacy-import checks.
