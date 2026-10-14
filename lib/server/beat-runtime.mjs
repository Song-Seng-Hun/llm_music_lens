import assets from 'virtual:music-lens-beat-assets';

// Pinned ONNX Runtime Emscripten C ABI; see prepare-server-beat.mjs.
let initialized, running = false;

function scope(runtime) {
  const pointers = new Set();
  return {
    alloc(size) { const pointer = runtime._malloc(size); if (!pointer) throw new Error(`Beat runtime allocation failed (${size} bytes)`); pointers.add(pointer); return pointer; },
    free(pointer) { if (pointers.delete(pointer)) runtime._free(pointer); },
    close() { for (const pointer of pointers) runtime._free(pointer); pointers.clear(); },
  };
}

function fail(runtime, memory, label) {
  const pointer = memory.alloc(8);
  runtime._OrtGetLastError(pointer, pointer + 4);
  throw new Error(`${label}: ${runtime.getValue(pointer, 'i32')} ${runtime.UTF8ToString(runtime.getValue(pointer + 4, '*'))}`);
}

function check(runtime, memory, code, label) { if (code !== 0) fail(runtime, memory, label); }
function string(runtime, memory, text) { const size = runtime.lengthBytesUTF8(text) + 1, pointer = memory.alloc(size); runtime.stringToUTF8(text, pointer, size); return pointer; }

async function initialize() {
  const { factory, compiled, modelBytes } = assets;
  const runtime = await factory({ numThreads: 1, instantiateWasm(imports, receive) {
    const instance = new WebAssembly.Instance(compiled, imports);
    receive(instance, compiled);
    return instance.exports;
  } });
  const memory = scope(runtime);
  let options, session;
  try {
    if (runtime.PTR_SIZE !== 4) throw new Error('Unsupported ONNX Runtime pointer ABI');
    check(runtime, memory, runtime._OrtInit(1, 2), 'OrtInit');
    const model = memory.alloc(modelBytes.byteLength);
    runtime.HEAPU8.set(new Uint8Array(modelBytes), model);
    options = runtime._OrtCreateSessionOptions(99, true, true, 0, false, 0, 0, 2, 0, 0);
    if (!options) fail(runtime, memory, 'CreateSessionOptions');
    session = await runtime._OrtCreateSession(model, modelBytes.byteLength, options);
    if (!session) fail(runtime, memory, 'CreateSession');
    memory.free(model);
    const counts = memory.alloc(8);
    check(runtime, memory, runtime._OrtGetInputOutputCount(session, counts, counts + 4), 'GetInputOutputCount');
    if (runtime.getValue(counts, 'i32') !== 1 || runtime.getValue(counts + 4, 'i32') !== 2) throw new Error('Unexpected Beat This input/output contract');
    return { runtime, session };
  } catch (error) {
    if (session) runtime._OrtReleaseSession(session);
    throw error;
  } finally {
    if (options) runtime._OrtReleaseSessionOptions(options);
    memory.close();
  }
}

async function getRuntime() {
  if (!initialized) initialized = initialize().catch(error => { initialized = undefined; throw error; });
  return initialized;
}

export async function runBeatFrames(features) {
  if (!(features instanceof Float32Array) || features.length !== 250 * 128) throw new TypeError('Beat model requires float32 features [1,250,128]');
  if (running) { const error = new Error('Beat runtime is busy; retry later'); error.code = 'BEAT_BUSY'; error.retryable = true; throw error; }
  running = true;
  let runtime, memory, tensor, runOptions;
  const outputHandles = [];
  try {
    const state = await getRuntime();
    runtime = state.runtime; memory = scope(runtime);
    const data = memory.alloc(features.byteLength), dimensions = memory.alloc(12);
    runtime.HEAPF32.set(features, data / 4);
    [1, 250, 128].forEach((size, index) => runtime.setValue(dimensions + index * 4, size, 'i32'));
    tensor = runtime._OrtCreateTensor(1, data, features.byteLength, dimensions, 3, 1);
    if (!tensor) fail(runtime, memory, 'CreateTensor');
    const inputNames = memory.alloc(4), inputValues = memory.alloc(4), outputNames = memory.alloc(8), outputValues = memory.alloc(8);
    runtime.setValue(inputNames, string(runtime, memory, 'features'), '*');
    runtime.setValue(inputValues, tensor, '*');
    ['beat', 'downbeat'].forEach((name, index) => { runtime.setValue(outputNames + index * 4, string(runtime, memory, name), '*'); runtime.setValue(outputValues + index * 4, 0, '*'); });
    runOptions = runtime._OrtCreateRunOptions(2, 0, false, 0);
    if (!runOptions) fail(runtime, memory, 'CreateRunOptions');
    const code = await runtime._OrtRun(state.session, inputNames, inputValues, 1, outputNames, 2, outputValues, runOptions);
    outputHandles.push(runtime.getValue(outputValues, '*'), runtime.getValue(outputValues + 4, '*'));
    check(runtime, memory, code, 'OrtRun');
    const outputs = {};
    for (let index = 0; index < 2; index++) {
      const meta = memory.alloc(16);
      check(runtime, memory, runtime._OrtGetTensorData(outputHandles[index], meta, meta + 4, meta + 8, meta + 12), 'GetTensorData');
      const type = runtime.getValue(meta, 'i32'), pointer = runtime.getValue(meta + 4, '*'), dims = runtime.getValue(meta + 8, '*'), rank = runtime.getValue(meta + 12, 'i32');
      const shape = Array.from({ length: rank }, (_, axis) => runtime.getValue(dims + axis * 4, 'i32'));
      runtime._OrtFree(dims);
      if (type !== 1 || shape.length !== 2 || shape[0] !== 1 || shape[1] !== 250) throw new Error('Unexpected Beat This output shape/type');
      outputs[['beat', 'downbeat'][index]] = runtime.HEAPF32.slice(pointer / 4, pointer / 4 + 250);
    }
    return { ...outputs, heapCapacityHighWaterBytes: runtime.HEAPU8.byteLength };
  } finally {
    if (runtime) {
      for (const output of outputHandles) if (output) runtime._OrtReleaseTensor(output);
      if (tensor) runtime._OrtReleaseTensor(tensor);
      if (runOptions) runtime._OrtReleaseRunOptions(runOptions);
      memory?.close();
    }
    running = false;
  }
}
