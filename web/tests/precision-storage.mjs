// Named failure: expanded ten-minute reports exceeded the old 1 MB cap.
// Exercise real D1 storage, byte refusal and owner isolation with exact cleanup.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { reportSchema, MAX_REPORT_BYTES } from '../lib/report.ts';
import { deriveMusicalFeatures } from '../public/analysis/musical-features.mjs';

const evidence = path.resolve('evidence/precision');
const report = JSON.parse(fs.readFileSync(path.join(evidence, 'browser-report.json'), 'utf8'));
const r = n => Math.round(n * 10000) / 10000;
report.source = { kind: 'synthetic', title: 'Dense detail storage fixture', duration: 600 };
report.signal.duration = 600;
report.detail.notes.items = Array.from({ length: 4000 }, (_, i) => ({ start: r(i * 0.15), end: r(i * 0.15 + 0.1), midi: 36 + i % 60, confidence: 0.75 }));
report.detail.notes.model = 'synthetic fixture';
report.detail.lyrics = { status: 'complete', model: 'synthetic fixture', language: 'korean', truncated: false, warnings: [], words: Array.from({ length: 2000 }, (_, i) => ({ start: r(i * 0.3), end: r(i * 0.3 + 0.2), text: '합성자료음악시간단서검증용단어' })) };
report.detail.rhythm.onsets = Array.from({ length: 6000 }, (_, i) => ({ time: r(i * 0.1), strength: 0.7, band: ['low', 'mid', 'high'][i % 3] }));
report.detail.rhythm.beats = Array.from({ length: 1200 }, (_, i) => i / 2);
report.detail.rhythm.envelope = Array.from({ length: 2400 }, (_, i) => ({ time: i / 4, low: 0.3123, mid: 0.4123, high: 0.2123, rms: 0.1123 }));
report.detail.rhythm.chords = Array.from({ length: 300 }, (_, i) => ({ start: i * 2, end: i * 2 + 2, label: 'C major', confidence: 0.5123 }));
report.detail.features = deriveMusicalFeatures(report.detail, 600);
report.signal.energy = report.detail.rhythm.envelope.map(({ time, rms }) => ({ time, rms }));
report.signal.chords = report.detail.rhythm.chords;
report.signal.tempo.beats = report.detail.rhythm.beats;
const data = JSON.stringify(reportSchema.parse(report));
const bytes = Buffer.byteLength(data);
assert.ok(bytes > 1_000_000 && bytes < MAX_REPORT_BYTES, `dense bytes: ${bytes}`);
const folder = path.resolve('.wrangler/state/v3/d1/miniflare-D1DatabaseObject');
const files = fs.readdirSync(folder).filter(x => /^[a-f0-9]{64}\.sqlite$/.test(x));
assert.equal(files.length, 1, 'Use only this project local D1');
const db = new DatabaseSync(path.join(folder, files[0])); db.exec('PRAGMA busy_timeout = 5000');
const base = 'http://127.0.0.1:5173', headers = { Cookie: '__sites_local_auth=1', 'content-type': 'application/json' };
const ids = [], checks = [];
try {
  const response = await fetch(base + '/api/reports', { method: 'POST', headers, body: data });
  assert.equal(response.status, 201, await response.clone().text());
  const saved = (await response.json()).report; ids.push({ id: saved.id, owner: 'local_seedy' });
  assert.deepEqual(saved.detail, report.detail);
  const read = await (await fetch(base + '/api/reports/' + saved.id, { headers })).json();
  assert.deepEqual(read.report.detail, report.detail);
  checks.push('dense report over old 1 MB cap saves and reads all detail');
  const over = await fetch(base + '/api/reports', { method: 'POST', headers, body: ' '.repeat(MAX_REPORT_BYTES) + '{}' });
  assert.equal(over.status, 413); checks.push('over 1.9 MB refused');
  const foreign = { ...report, id: crypto.randomUUID() };
  db.prepare('INSERT INTO reports (id, owner_id, title, source_type, created_at, body) VALUES (?, ?, ?, ?, ?, ?)').run(foreign.id, 'precision_other', report.source.title, 'synthetic', report.createdAt, JSON.stringify(foreign));
  ids.push({ id: foreign.id, owner: 'precision_other' });
  const list = await (await fetch(base + '/api/reports', { headers })).json();
  assert.ok(!list.reports.some(x => x.id === foreign.id));
  assert.equal((await fetch(base + '/api/reports/' + foreign.id, { headers })).status, 404);
  const rpc = await (await fetch(base + '/mcp', { method: 'POST', headers, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'get_music_report', arguments: { id: foreign.id } } }) })).json();
  assert.equal(rpc.result.isError, true); checks.push('foreign detail excluded from list and refused by REST/MCP');
  fs.writeFileSync(path.join(evidence, 'storage-checks.json'), JSON.stringify({ bytes, cap: MAX_REPORT_BYTES, checks }, null, 2));
  console.log(JSON.stringify({ bytes, checks }));
} finally {
  for (const { id, owner } of ids) db.prepare('DELETE FROM reports WHERE id = ? AND owner_id = ? AND title = ?').run(id, owner, report.source.title);
  db.close();
}
