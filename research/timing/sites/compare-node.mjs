import { lstat, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import * as ort from 'onnxruntime-node';

const { values }=parseArgs({options:{'artifact-dir':{type:'string'},help:{type:'boolean',short:'h'}}});
if(values.help){console.log('Usage: node research/timing/sites/compare-node.mjs --artifact-dir DIR');process.exit(0);}
if(!values['artifact-dir'])throw new Error('--artifact-dir is required');
const artifact = name => resolve(values['artifact-dir'],name);
const resultPath = artifact('beat-node-result.json');
const exists = await lstat(resultPath).then(() => true, error => { if (error.code === 'ENOENT') return false; throw error; });
if (exists) throw new Error('beat-node-result.json already exists; previous results are never overwritten');
const inputBytes = await readFile(artifact('beat-input.f32'));
const input = new Float32Array(inputBytes.buffer.slice(inputBytes.byteOffset, inputBytes.byteOffset + inputBytes.byteLength));
const expected = JSON.parse(await readFile(artifact('beat-reference.json'), 'utf8'));
const started = performance.now();
const session = await ort.InferenceSession.create(artifact('beat-small-250.onnx'), { executionProviders: ['cpu'], intraOpNumThreads: 1, interOpNumThreads: 1, graphOptimizationLevel: 'all', enableCpuMemArena: true, enableMemPattern: true });
const loaded = performance.now();
const output = await session.run({ features: new ort.Tensor('float32', input, [1, 250, 128]) });
const finished = performance.now();
const result = { runtime: 'onnxruntime-node', versions: ort.env.versions, initMs: loaded-started, runMs: finished-loaded, outputs: {} };
for (const name of ['beat', 'downbeat']) {
  const actual = Array.from(output[name].data), ref = expected[name];
  const maxAbs = Math.max(...actual.map((v,i)=>Math.abs(v-ref[i])));
  const passed = actual.length === ref.length && actual.every((v,i)=>Number.isFinite(v) && Math.abs(v-ref[i]) <= 1e-4 + 1e-4*Math.abs(ref[i]));
  result.outputs[name] = { shape: output[name].dims, length: actual.length, maxAbs, passed };
}
await session.release();
await writeFile(resultPath, JSON.stringify(result, null, 2), { flag: 'wx' });
console.log(JSON.stringify(result));
if (Object.values(result.outputs).some(v=>!v.passed)) process.exitCode=1;
