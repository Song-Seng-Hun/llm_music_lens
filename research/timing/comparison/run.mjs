// Fixed paired experiment: preprocessing gate, old/reference run, actual MCP run.
import { readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createHash } from 'node:crypto';
import { parseArgs } from 'node:util';
import { analyzeRhythm } from '../../../web/public/precision-rhythm.mjs';
import { beatFeatures } from '../../../web/lib/server/beat-frontend.mjs';
import { analyzeBeatPcm } from '../../../web/lib/server/beat-analysis.mjs';

const { values } = parseArgs({ options: {
  root: { type: 'string' }, mode: { type: 'string' }, model: { type: 'string' },
  endpoint: { type: 'string' }, headers: { type: 'string' },
} });
if (!values.root || !['parity', 'reference', 'production'].includes(values.mode)) throw new Error('Use --root PATH --mode parity|reference|production; reference requires --model; production requires --endpoint and optional --headers JSON.');
const root = resolve(values.root), mode = values.mode;
const hash = data => createHash('sha256').update(data).digest('hex');
const readJson = async path => JSON.parse(await readFile(path, 'utf8'));
const data = await readJson(join(root, 'data.json'));
const featureInfo = await readJson(join(root, 'features.json'));
const output = { mode, createdAt: new Date().toISOString(), cases: [], failures: [] };
if (mode === 'parity') {
  output.tolerance = { absolute: 2e-4, relative: 1e-4 };
  output.frontendSha256 = hash(await readFile(resolve('web/lib/server/beat-frontend.mjs')));
  try { output.constantsSha256 = hash(await readFile(resolve('web/lib/server/beat-frontend-constants.mjs'))); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
}
const outputPath = join(root, `${mode}.json`);
// Reserve once so an interrupted or failed run is retained rather than overwritten.
await writeFile(outputPath, JSON.stringify(output, null, 2), { flag: 'wx' });
const persist = () => writeFile(outputPath, JSON.stringify(output, null, 2));
const pcmOf = bytes => Float32Array.from({ length: bytes.length / 2 }, (_, i) => bytes.readInt16LE(i * 2) / 32768);
const floatOf = bytes => new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));

let session, ort, frozen;
if (mode !== 'parity') {
  frozen = await readJson(join(root, 'FREEZE.json'));
  for (const [path, expected] of Object.entries(frozen.files)) {
    if (hash(await readFile(resolve(path))) !== expected) throw new Error(`Frozen file changed: ${path}`);
  }
  output.freezeSha256 = hash(await readFile(join(root, 'FREEZE.json')));
}
if (mode === 'reference') {
  if (!values.model || hash(await readFile(values.model)) !== frozen.modelSha256) throw new Error('Pinned --model required');
  const start = performance.now();
  ort = await import('onnxruntime-node');
  session = await ort.InferenceSession.create(resolve(values.model), { executionProviders: ['cpu'], intraOpNumThreads: 1, interOpNumThreads: 1, graphOptimizationLevel: 'all' });
  output.modelLoadMs = performance.now() - start;
  output.runtime = ort.env.versions;
}
let headers = {};
if (mode === 'production') {
  const endpoint = new URL(values.endpoint);
  if (endpoint.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(endpoint.hostname)) throw new Error('Comparison only calls an explicit local HTTP MCP endpoint');
  headers = values.headers ? await readJson(values.headers) : {};
  output.endpoint = values.endpoint;
}

for (const item of data.cases) {
  const record = { key: item.key, pcmSha256: item.pcm_sha256 };
  const started = performance.now();
  try {
    const bytes = await readFile(join(root, item.pcm_path));
    if (hash(bytes) !== item.pcm_sha256) throw new Error('PCM identity changed');
    const pcm = pcmOf(bytes), feature = featureInfo.cases.find(value => value.key === item.key);
    const featureBytes = await readFile(join(root, feature.path));
    if (hash(featureBytes) !== feature.sha256) throw new Error('Official features changed');
    const official = floatOf(featureBytes), frameCount = feature.shape[0];
    if (mode === 'parity') {
      const actual = beatFeatures(pcm, 22050);
      if (actual.frameCount !== frameCount || actual.features.length !== official.length) throw new Error('Frontend shape mismatch');
      let maxAbs = 0, mismatches = 0;
      for (let i = 0; i < official.length; i++) {
        const error = Math.abs(actual.features[i] - official[i]);
        maxAbs = Math.max(maxAbs, error);
        if (!Number.isFinite(actual.features[i]) || error > 2e-4 + 1e-4 * Math.abs(official[i])) mismatches++;
      }
      Object.assign(record, { frameCount, values: official.length, maxAbs, mismatches, passed: mismatches === 0 });
      if (mismatches) output.failures.push(item.key);
    } else if (mode === 'reference') {
      const oldStart = performance.now();
      const old = analyzeRhythm(pcm, 22050);
      record.old = { beats: old.beats, bpm: old.bpm, milliseconds: performance.now() - oldStart, rssBytes: process.memoryUsage().rss };
      const modelStart = performance.now();
      const candidate = await analyzeBeatPcm(pcm, { sampleRate: 22050, runFrames: async chunk => {
        const logits = await session.run({ features: new ort.Tensor('float32', chunk, [1, 250, 128]) });
        return { beat: logits.beat.data, downbeat: logits.downbeat.data };
      } });
      record.reference = { ...candidate, milliseconds: performance.now() - modelStart,
        frontend: 'actual shared production JS', runtime: 'NodeORT CPU runtime reference',
        rssBytes: process.memoryUsage().rss };
    } else {
      const response = await fetch(values.endpoint, { method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
        body: JSON.stringify({ jsonrpc: '2.0', id: item.key, method: 'tools/call', params: {
          name: 'analyze_music_timing', arguments: { audio: { encoding: 'pcm_s16le', sampleRate: 22050, channels: 1, data: bytes.toString('base64') }, offsetSeconds: 0 },
        } }), signal: AbortSignal.timeout(120000) });
      const text = await response.text();
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${text.slice(0, 400)}`);
      let rpc;
      if (text.trimStart().startsWith('{')) rpc = JSON.parse(text);
      else {
        const events = text.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => JSON.parse(line.slice(5)));
        rpc = events.find(event => event.id === item.key);
      }
      if (rpc?.error || rpc?.result?.isError) throw new Error(JSON.stringify(rpc.error ?? rpc.result));
      const result = rpc?.result?.structuredContent?.result;
      if (!result?.timing) throw new Error('MCP response lacks structuredContent.result.timing');
      record.responseProvenance = { pcmSha256: hash(bytes), inputFrames: pcm.length, response: rpc.result.structuredContent };
      if (result.source?.sha256 !== item.pcm_sha256 || result.source?.sampleRate !== 22050 || result.source?.duration !== item.duration_seconds || result.source?.offsetSeconds !== 0) throw new Error('Production source identity/duration does not match frozen PCM');
      record.production = result.timing;
    }
  } catch (error) {
    record.error = `${error.name}: ${error.message}`;
    output.failures.push(item.key);
  }
  record.wallMs = performance.now() - started;
  output.cases.push(record);
  await persist();
  console.log(JSON.stringify({ key: item.key, mode, passed: !record.error && record.passed !== false, milliseconds: record.wallMs }));
}
if (session) await session.release();
output.completedAt = new Date().toISOString();
await persist();
if (output.failures.length) process.exitCode = 1;
