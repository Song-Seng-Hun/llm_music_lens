import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, copyFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: {
  'artifact-dir': { type: 'string' }, 'work-dir': { type: 'string' },
  run: { type: 'boolean', default: false }, help: { type: 'boolean', short: 'h' },
} });
if (values.help) {
  console.log('Usage: node research/timing/sites/prepare-workerd.mjs --artifact-dir DIR --work-dir DIR [--run]\nDIR artifacts: beat-small-250.onnx, beat-input.f32, beat-reference.json, beat-export.json.\nwork-dir must be new and inside this checkout\'s ignored evidence/ directory. Generated loader/config only; models are referenced in place.');
  process.exit(0);
}
if (!values['artifact-dir'] || !values['work-dir']) throw new Error('--artifact-dir and --work-dir are required');
const root = fileURLToPath(new URL('../../../', import.meta.url));
const here = dirname(fileURLToPath(import.meta.url));
const artifacts = resolve(values['artifact-dir']), work = resolve(values['work-dir']);
const evidence = resolve(root, 'evidence'), relativeWork = relative(evidence, work);
if (!relativeWork || isAbsolute(relativeWork) || relativeWork.startsWith(`..${sep}`) || relativeWork === '..') throw new Error('--work-dir must be a subdirectory of the ignored evidence/ directory');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const ortRoot = resolve(root, 'node_modules/onnxruntime-web');
const version = JSON.parse(await readFile(resolve(ortRoot, 'package.json'), 'utf8')).version;
const factoryPath = resolve(ortRoot, 'dist/ort-wasm-simd-threaded.mjs');
const wasmPath = resolve(ortRoot, 'dist/ort-wasm-simd-threaded.wasm');
const factory = await readFile(factoryPath), wasm = await readFile(wasmPath);
if (version !== '1.22.0-dev.20250409-89f8206ba4' || sha(factory) !== '43c25054b6b9ac000f786c65545ff83a45f871e0e310e8c2f4d48a363bb66db4' || sha(wasm) !== 'f061472c6e77d6d50d079aacdc0ff9b63fee287ddd2cbf46cf62438d3891de2b') throw new Error('Installed ORT bytes/version differ from the tested runtime; review before regenerating adapter');
const modelPath=resolve(artifacts,'beat-small-250.onnx'), inputPath=resolve(artifacts,'beat-input.f32'), referencePath=resolve(artifacts,'beat-reference.json');
const metadata=JSON.parse(await readFile(resolve(artifacts,'beat-export.json'),'utf8'));
const model=await readFile(modelPath), input=await readFile(inputPath), referenceBytes=await readFile(referencePath);
const reference=JSON.parse(referenceBytes);
if (metadata.checkpoint_sha256 !== '6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b' || JSON.stringify(metadata.shape) !== '[1,250,128]' || metadata.modelSha256 !== sha(model) || metadata.inputSha256 !== sha(input) || input.length !== 1*250*128*4) throw new Error('Artifacts fail fixed-checkpoint/input hash/size validation');
if (['beat','downbeat'].some(name=>!Array.isArray(reference[name]) || reference[name].length!==250 || !reference[name].every(Number.isFinite))) throw new Error('Reference must contain 250 finite logits per output');
const require=createRequire(import.meta.url), workerd=require('workerd');
if(workerd.version!=='1.20260515.1') throw new Error(`Untested workerd version ${workerd.version}`);
await mkdir(dirname(work),{recursive:true});
// Refuse existing work directories so an earlier receipt can never be overwritten.
await mkdir(work);
await writeFile(resolve(work,'ort-factory-adapted.mjs'),'// Research adapter: suppress Node/browser environment branches; instantiate precompiled WASM.\nconst process = undefined;\nconst WorkerGlobalScope = undefined;\n'+factory.toString('utf8'));
await copyFile(resolve(here,'beat-workerd.mjs'),resolve(work,'beat-workerd.mjs'));
const pathString = path => JSON.stringify(relative(work,path).split(sep).join('/'));
const config = `using Workerd = import ${pathString(resolve(root,'node_modules/workerd/workerd.capnp'))};
const config :Workerd.Config = (services = [(name = "beat-small-250", worker = (
  compatibilityDate = "2026-05-15", compatibilityFlags = ["nodejs_compat"],
  modules = [
    (name = "worker.mjs", esModule = embed "beat-workerd.mjs"),
    (name = "ort-factory.mjs", esModule = embed "ort-factory-adapted.mjs"),
    (name = "ort.wasm", wasm = embed ${pathString(wasmPath)}),
    (name = "beat-model.onnx", data = embed ${pathString(modelPath)}),
    (name = "beat-input.f32", data = embed ${pathString(inputPath)}),
    (name = "beat-reference.json", json = embed ${pathString(referencePath)})
  ]
))]);\n`;
const configPath=resolve(work,'beat-workerd.capnp');
await writeFile(configPath,config);
const manifest={ortVersion:version,workerdVersion:workerd.version,factorySha256:sha(factory),wasmSha256:sha(wasm),modelSha256:sha(model),inputSha256:sha(input),referenceSha256:sha(referenceBytes),workerSha256:sha(await readFile(resolve(work,'beat-workerd.mjs'))),configPath,command:[workerd.default,'test',configPath]};
await writeFile(resolve(work,'prepared.json'),JSON.stringify(manifest,null,2));
console.log(JSON.stringify(manifest));
if(values.run){
  const run=spawnSync(workerd.default,['test',configPath],{encoding:'utf8',windowsHide:true,timeout:60_000,maxBuffer:4*1024*1024});
  await writeFile(resolve(work,'workerd.log'),(run.stdout??'')+(run.stderr??'')+(run.error?String(run.error):''));
  const line=(run.stdout??'').split(/\r?\n/).find(v=>v.startsWith('{"purpose":'));
  const result=line?JSON.parse(line):null;
  await writeFile(resolve(work,'workerd-result.json'),JSON.stringify({exitCode:run.status,signal:run.signal,result},null,2));
  console.log(JSON.stringify({exitCode:run.status,signal:run.signal,ok:result?.ok,resultPath:resolve(work,'workerd-result.json'),heapCapacityHighWaterBytes:result?.heapCapacityHighWaterBytes,outputs:result?Object.fromEntries(Object.entries(result.outputs).map(([name,{logits,...summary}])=>[name,summary])):null}));
  if(run.status!==0||result?.ok!==true)process.exitCode=1;
}
