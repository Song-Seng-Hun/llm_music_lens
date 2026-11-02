// Local built-Worker acceptance only; identity headers below are test fixtures,
// not credentials or proof of hosted Sites authentication.
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { relative, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { parseArgs } from 'node:util';
import assert from 'node:assert/strict';
const { values } = parseArgs({ options: { endpoint: { type: 'string' }, 'built-worker': { type: 'string' }, pcm: { type: 'string' }, vocal: { type: 'string' }, output: { type: 'string' }, 'source-kind': { type: 'string', default: 'mixture' } } });
assert.ok(['mixture', 'isolated-vocal'].includes(values['source-kind']));
for (const key of ['pcm', 'vocal', 'output']) if (!values[key]) throw new Error(`Missing --${key}`);
if (Boolean(values.endpoint) === Boolean(values['built-worker'])) throw new Error('Choose exactly one of --endpoint URL or --built-worker DIR');
const url = new URL(values.endpoint ?? 'http://localhost/mcp');
if (!['127.0.0.1', 'localhost'].includes(url.hostname)) throw new Error('This fixture test is local-only');
const headers = { 'content-type': 'application/json', 'oai-authenticated-user-id': 'timing-integration-fixture', 'oai-authenticated-user-email': 'fixture@example.invalid' };
let id = 0, runtime;
let dispatch = fetch;
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = { endpoint: url.href, transport: values['built-worker'] ? 'immutable-built-worker/miniflare' : 'http', cases: [], result: null };

async function compiledModules(directory) {
  const modules = [];
  for (const item of await readdir(directory, { withFileTypes: true })) {
    if (item.name.startsWith('.')) continue;
    const path = resolve(directory, item.name);
    if (item.isDirectory()) modules.push(...await compiledModules(path));
    else if (/\.(?:m?js|wasm|bin)$/.test(item.name)) {
      const type = item.name.endsWith('.wasm') ? 'CompiledWasm' : item.name.endsWith('.bin') ? 'Data' : 'ESModule';
      const bytes = await readFile(path);
      modules.push({ type, path, contents: type === 'ESModule' ? bytes.toString('utf8') : bytes });
    }
  }
  return modules;
}

async function request(method, params, extraHeaders = headers) {
  const r = await dispatch(url.href, { method: 'POST', headers: extraHeaders, body: JSON.stringify({ jsonrpc: '2.0', id: ++id, method, params }) });
  const text = await r.text();
  try { return { status: r.status, value: JSON.parse(text) }; }
  catch { throw new Error(`HTTP ${r.status}: ${text.slice(0, 1000)}`); }
}
try {
  if (values['built-worker']) {
    const root = resolve(values['built-worker']);
    const config = JSON.parse(await readFile(resolve(root, 'wrangler.json'), 'utf8'));
    const entry = resolve(root, config.main);
    const modules = await compiledModules(root);
    assert.ok(modules.some(module => module.path === entry), 'Built Worker entry must exist');
    modules.sort((a, b) => a.path === entry ? -1 : b.path === entry ? 1 : a.path.localeCompare(b.path));
    receipt.build = { root, compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags, modules: Object.fromEntries(modules.map(module => [relative(root, module.path).replaceAll('\\', '/'), { type: module.type, sha256: sha256(module.contents) }])) };
    const { Miniflare } = await import('miniflare');
    // Eager contents snapshot: no Wrangler proxy, watcher, retries or fallback.
    runtime = new Miniflare({ modulesRoot: root, modules, compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags, d1Databases: (config.d1_databases ?? []).map(database => database.binding), cf: false });
    dispatch = (input, init) => runtime.dispatchFetch(input, init);
  }
  const discovery = await request('server/discover');
  assert.equal(discovery.status, 200);
  assert.deepEqual(discovery.value.result.supportedVersions, ['2026-07-28']);
  receipt.cases.push({ name: 'Sites discovery', status: discovery.status, pass: true });
  const denied = await request('tools/call', { name: 'analyze_music_timing', arguments: {} }, { 'content-type': 'application/json' });
  assert.equal(denied.status, 401);
  receipt.cases.push({ name: 'Unauthenticated analysis refused', status: denied.status, pass: true });
  const cross = await request('ping', {}, { ...headers, origin: 'https://different.invalid' });
  assert.equal(cross.status, 403);
  receipt.cases.push({ name: 'Cross-origin refused', status: cross.status, pass: true });
  const invalid = await request('tools/call', { name: 'analyze_music_timing', arguments: { audio: { encoding: 'pcm_s16le', sampleRate: 48000, channels: 1, data: 'AAAA' } } });
  assert.equal(invalid.status, 200);
  assert.equal(invalid.value.result.isError, true);
  receipt.cases.push({ name: 'Unsupported PCM format refused', status: invalid.status, pass: true });
  const vocalBytes = await readFile(resolve(values.vocal));
  const vocal = JSON.parse(vocalBytes.toString('utf8'));
  const pcm = await readFile(resolve(values.pcm));
  receipt.inputs = { pcmSha256: sha256(pcm), vocalSha256: sha256(vocalBytes), sourceKind: values['source-kind'] };
  const result = await request('tools/call', { name: 'analyze_music_timing', arguments: {
    audio: { encoding: 'pcm_s16le', sampleRate: 22050, channels: 1, data: pcm.toString('base64') },
    timedVocal: vocal, offsetSeconds: 0, sourceKind: values['source-kind'],
  } });
  assert.equal(result.status, 200); assert.equal(result.value.result.isError, false);
  const analysis = result.value.result.structuredContent.result;
  assert.match(analysis.timing.provenance.runtime, /workerd/);
  assert.equal(analysis.vocalTiming.status, 'linked-supplied-timing');
  assert.equal(analysis.vocalTiming.unit, vocal.unit);
  assert.deepEqual(analysis.vocalTiming.items.map(({ start, end, text }) => ({ start, end, text })), vocal.items);
  assert.ok(analysis.unavailable.includes('server acoustic syllable alignment'));
  assert.equal(analysis.waveform.sourceKind, values['source-kind']);
  assert.equal(analysis.waveform.analysisWindowSeconds, 0.02);
  assert.equal(analysis.waveform.analysisHopSeconds, 0.01);
  assert.equal(analysis.waveform.curve.stepSeconds, 0.1);
  assert.ok(analysis.waveform.curve.rmsDbfs.length > 0);
  assert.equal(analysis.waveform.context.total, analysis.waveform.activity.regions.length);
  assert.equal(analysis.waveform.context.items.length, Math.min(16, analysis.waveform.context.total));
  assert.equal(analysis.waveform.context.truncated, analysis.waveform.context.total > 16);
  assert.ok(Buffer.byteLength(JSON.stringify(analysis.waveform)) <= 50000);
  receipt.cases.push({ name: 'Actual model plus supplied phone timing linked, originals intact', status: result.status, count: vocal.items.length, pass: true });
  receipt.cases.push({ name: 'Actual server waveform evidence returned with caller attribution and bounded summary', status: result.status, pass: true });
  assert.equal(analysis.acoustics.schema, 'spectral-texture/1');
  assert.equal(analysis.acoustics.sourceKind, values['source-kind']);
  assert.equal(analysis.acoustics.analysis.fftSize, 1024);
  assert.equal(analysis.acoustics.windows.length, Math.ceil(pcm.length / 2 / 22050));
  assert.ok(analysis.acoustics.windows.length <= 30);
  assert.ok(analysis.acoustics.windows.some(window => window.centroidHz !== null));
  assert.ok(!('acoustics' in analysis.timing), 'spectral output has one owning field');
  const withoutSpectral = structuredClone(result.value);
  delete withoutSpectral.result.structuredContent.result.acoustics;
  const oldText = JSON.parse(withoutSpectral.result.content[0].text);
  delete oldText.acoustics;
  withoutSpectral.result.content[0].text = JSON.stringify(oldText);
  receipt.addedSpectralWireBytes = Buffer.byteLength(JSON.stringify(result.value)) - Buffer.byteLength(JSON.stringify(withoutSpectral));
  assert.ok(receipt.addedSpectralWireBytes <= 30000);
  receipt.cases.push({ name: 'Actual shared FFT spectral texture reaches MCP with bounded wire bytes', status: result.status, pass: true });
  assert.ok(analysis.attacks.total > 0);
  assert.equal(analysis.attacks.events.length, Math.min(64, analysis.attacks.total));
  assert.equal(analysis.attacks.truncated, analysis.attacks.total > 64);
  assert.equal(analysis.attacks.density.reduce((sum, window) => sum + window.count, 0), analysis.attacks.total);
  assert.equal(analysis.attacks.sourceKind, values['source-kind']);
  assert.ok(!('attackCandidates' in analysis.timing));
  assert.ok(analysis.attacks.events.every(event => event.time >= 0 && event.time < analysis.source.duration));
  const withoutAttacks = structuredClone(result.value);
  delete withoutAttacks.result.structuredContent.result.attacks;
  const withoutAttackText = JSON.parse(withoutAttacks.result.content[0].text);
  delete withoutAttackText.attacks;
  withoutAttacks.result.content[0].text = JSON.stringify(withoutAttackText);
  receipt.addedAttackWireBytes = Buffer.byteLength(JSON.stringify(result.value)) - Buffer.byteLength(JSON.stringify(withoutAttacks));
  assert.ok(receipt.addedAttackWireBytes <= 50000);
  receipt.cases.push({ name: 'Actual spectral attacks link to beats with capped detail and complete density', status: result.status, pass: true });
  if (values['source-kind'] === 'mixture') assert.equal(analysis.pitch.status, 'unavailable');
  else {
    assert.equal(analysis.pitch.status, 'estimated');
    assert.equal(analysis.pitch.sourceKind, 'isolated-vocal');
    assert.equal(analysis.pitch.contour.total, Math.ceil(pcm.length / 2 / 441));
    assert.equal(analysis.pitch.contour.hz.length, analysis.pitch.contour.total);
    assert.equal(analysis.pitch.contour.midi.length, analysis.pitch.contour.total);
    assert.equal(analysis.pitch.contour.periodicity.length, analysis.pitch.contour.total);
    assert.ok(analysis.pitch.contour.hz.every(value => value === null || Number.isFinite(value) && value >= 70 && value <= 1000));
    assert.equal(analysis.pitch.contour.hz[0], null); assert.equal(analysis.pitch.contour.truncated, false);
    assert.ok(analysis.pitch.summary.estimatedFrames > 0);
    assert.ok(analysis.pitch.summary.mostObservedNotes.length <= 12);
  }
  const withoutPitch = structuredClone(result.value);
  delete withoutPitch.result.structuredContent.result.pitch;
  const withoutPitchText = JSON.parse(withoutPitch.result.content[0].text);
  delete withoutPitchText.pitch; withoutPitch.result.content[0].text = JSON.stringify(withoutPitchText);
  receipt.addedPitchWireBytes = Buffer.byteLength(JSON.stringify(result.value)) - Buffer.byteLength(JSON.stringify(withoutPitch));
  assert.ok(receipt.addedPitchWireBytes <= 80000);
  receipt.cases.push({ name: `Actual vocal pitch source gate, complete contour and wire budget (${values['source-kind']})`, status: result.status, pass: true });
  receipt.result = analysis;
  receipt.pass = true;
} catch (error) {
  receipt.pass = false; receipt.error = String(error); process.exitCode = 1;
} finally {
  try { await runtime?.dispose(); }
  catch (error) { receipt.pass = false; receipt.cleanupError = String(error); process.exitCode = 1; }
  await writeFile(resolve(values.output), JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ pass: receipt.pass, cases: receipt.cases, error: receipt.error, output: values.output }));
}
