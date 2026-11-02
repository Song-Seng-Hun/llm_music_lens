import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const root = fileURLToPath(new URL('../', import.meta.url));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const EXPECTED = {
  ort: '1.22.0-dev.20250409-89f8206ba4',
  factory: '43c25054b6b9ac000f786c65545ff83a45f871e0e310e8c2f4d48a363bb66db4',
  wasm: 'f061472c6e77d6d50d079aacdc0ff9b63fee287ddd2cbf46cf62438d3891de2b',
  model: '2c61c460654723857fc8dface211d133e6c983efec4c5e91b576bfca746182f1',
};

async function preserveOrWrite(path, bytes) {
  try {
    const existing = await readFile(path);
    if (sha(existing) !== sha(bytes)) throw new Error(`Prepared asset differs: ${path}. Review/remove that generated file explicitly before rebuilding.`);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    await writeFile(path, bytes, { flag: 'wx' });
  }
}

/** Prepare the actual Worker assets from tracked model + installed pinned ORT. */
export async function prepareServerBeat({ modelPath = resolve(root, 'models/beat-this/small0-250.bin') } = {}) {
  const ort = resolve(root, 'node_modules/onnxruntime-web');
  const version = JSON.parse(await readFile(resolve(ort, 'package.json'), 'utf8')).version;
  const factory = await readFile(resolve(ort, 'dist/ort-wasm-simd-threaded.mjs'));
  const wasm = await readFile(resolve(ort, 'dist/ort-wasm-simd-threaded.wasm'));
  const model = await readFile(modelPath);
  if (version !== EXPECTED.ort || sha(factory) !== EXPECTED.factory || sha(wasm) !== EXPECTED.wasm || sha(model) !== EXPECTED.model) throw new Error('Beat This / ORT pinned asset hash or version mismatch');
  const directory = resolve(root, '.sites-runtime/server-beat');
  await mkdir(directory, { recursive: true });
  const loader = Buffer.concat([Buffer.from('// Emscripten environment adapter for statically compiled Workers WASM.\nconst process = undefined;\nconst WorkerGlobalScope = undefined;\n'), factory]);
  await preserveOrWrite(resolve(directory, 'ort-factory.mjs'), loader);
  await preserveOrWrite(resolve(directory, 'ort.wasm'), wasm);
  await preserveOrWrite(resolve(directory, 'beat-small-250.bin'), model);
  const manifest = { model: 'Beat This small0', shape: [1, 250, 128], opset: 17, ortVersion: version, modelSha256: sha(model), upstreamFactorySha256: sha(factory), adaptedFactorySha256: sha(loader), wasmSha256: sha(wasm), modelBytes: model.length };
  await preserveOrWrite(resolve(directory, 'manifest.json'), Buffer.from(JSON.stringify(manifest, null, 2) + '\n'));
  return { directory, ...manifest };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({ options: { model: { type: 'string' }, help: { type: 'boolean', short: 'h' } } });
  if (values.help) console.log('Usage: node scripts/prepare-server-beat.mjs [--model PATH]\nDefault: tracked models/beat-this/small0-250.bin. An explicit model override must match the same pinned hash. No network, Python or model export is needed at build time.');
  else console.log(JSON.stringify(await prepareServerBeat({ ...(values.model ? { modelPath: resolve(values.model) } : {}) })));
}
