import factory from './ort-factory.mjs';
import compiled from './ort.wasm';
import modelBytes from './beat-model.onnx';
import featureBytes from './beat-input.f32';
import reference from './beat-reference.json';

// Isolated feasibility caller of the installed ORT Emscripten C API.
// ABI follows node_modules/onnxruntime-web/lib/wasm/{wasm-types,wasm-core-impl}.ts.
export default {
  async test() {
    const result = { purpose: 'actual fixed Beat This inference, not downbeat quality', shape: [1,250,128], heapStages: {}, outputs: {}, timeNote: 'Worker clocks may be fixed within an event; whole test time is also in workerd log', peakNote: 'WASM capacity after stages is not whole-isolate peak or live tensor bytes' };
    let runtime, session, options, tensor, outputHandles = [], runOptions, stage = 'init';
    const allocations = [];
    const started = performance.now();
    try {
      runtime = await factory({ numThreads: 1, instantiateWasm(imports, receive) { const instance = new WebAssembly.Instance(compiled, imports); receive(instance, compiled); return instance.exports; } });
      const r = runtime, ptr = r.PTR_SIZE;
      if (ptr !== 4) throw new Error(`Unexpected ABI pointer size ${ptr}`);
      const alloc = n => { const p = r._malloc(n); if (!p) throw new Error(`malloc failed: ${n}`); allocations.push(p); return p; };
      const error = label => { const p = alloc(8); r._OrtGetLastError(p,p+4); throw new Error(`${label}: ${r.getValue(p,'i32')} ${r.UTF8ToString(r.getValue(p+4,'*'))}`); };
      const checked = (code,label) => { if (code !== 0) error(label); };
      const string = text => { const p=alloc(r.lengthBytesUTF8(text)+1); r.stringToUTF8(text,p,r.lengthBytesUTF8(text)+1); return p; };
      checked(r._OrtInit(1,2),'OrtInit');
      result.heapStages.init = r.HEAPU8.byteLength;
      stage = 'session';
      const model = alloc(modelBytes.byteLength);
      r.HEAPU8.set(new Uint8Array(modelBytes),model);
      options = r._OrtCreateSessionOptions(99,true,true,0,false,0,0,2,0,0);
      if (!options) error('CreateSessionOptions');
      session = await r._OrtCreateSession(model,modelBytes.byteLength,options);
      if (!session) error('CreateSession');
      // Match upstream createSession: serialized model buffer is no longer needed.
      r._free(model);
      allocations.splice(allocations.indexOf(model),1);
      result.heapStages.session = r.HEAPU8.byteLength;
      result.modelBytes = modelBytes.byteLength;
      const counts = alloc(8);
      checked(r._OrtGetInputOutputCount(session,counts,counts+4),'GetInputOutputCount');
      result.ioCount = [r.getValue(counts,'i32'),r.getValue(counts+4,'i32')];
      if (result.ioCount[0] !== 1 || result.ioCount[1] !== 2) throw new Error('Unexpected model IO');
      stage = 'tensor';
      const data=alloc(featureBytes.byteLength), dims=alloc(12);
      r.HEAPU8.set(new Uint8Array(featureBytes),data);
      [1,250,128].forEach((n,i)=>r.setValue(dims+i*4,n,'i32'));
      tensor = r._OrtCreateTensor(1,data,featureBytes.byteLength,dims,3,1);
      if (!tensor) error('CreateTensor');
      const inputNames=alloc(4),inputValues=alloc(4),outputNames=alloc(8),outputValues=alloc(8);
      r.setValue(inputNames,string('features'),'*');
      r.setValue(inputValues,tensor,'*');
      ['beat','downbeat'].forEach((name,i)=>{r.setValue(outputNames+i*4,string(name),'*');r.setValue(outputValues+i*4,0,'*');});
      runOptions = r._OrtCreateRunOptions(2,0,false,0);
      if (!runOptions) error('CreateRunOptions');
      stage = 'inference';
      const inferStarted=performance.now();
      checked(await r._OrtRun(session,inputNames,inputValues,1,outputNames,2,outputValues,runOptions),'OrtRun');
      result.inferenceClockMs=performance.now()-inferStarted;
      result.heapStages.inference = r.HEAPU8.byteLength;
      stage = 'output';
      outputHandles=[r.getValue(outputValues,'*'),r.getValue(outputValues+4,'*')];
      for (let i=0;i<2;i++) {
        const name=['beat','downbeat'][i], meta=alloc(16);
        checked(r._OrtGetTensorData(outputHandles[i],meta,meta+4,meta+8,meta+12),'GetTensorData');
        const type=r.getValue(meta,'i32'), dataPtr=r.getValue(meta+4,'*'), dimsPtr=r.getValue(meta+8,'*'), rank=r.getValue(meta+12,'i32');
        const shape=Array.from({length:rank},(_,j)=>r.getValue(dimsPtr+j*4,'i32'));
        r._OrtFree(dimsPtr);
        if(type!==1) throw new Error(`Unexpected output type ${type}`);
        const count=shape.reduce((a,b)=>a*b,1), actual=Array.from(r.HEAPF32.subarray(dataPtr/4,dataPtr/4+count)), expected=reference[name];
        const passed=count===expected.length && actual.every((v,j)=>Number.isFinite(v)&&Math.abs(v-expected[j])<=1e-4+1e-4*Math.abs(expected[j]));
        result.outputs[name]={shape,count,logits:actual,maxAbs:Math.max(...actual.map((v,j)=>Math.abs(v-expected[j]))),passed};
      }
      result.ok=Object.values(result.outputs).every(v=>v.passed);
      result.heapStages.output = r.HEAPU8.byteLength;
      result.heapCapacityHighWaterBytes=Math.max(...Object.values(result.heapStages));
      result.workerClockMs=performance.now()-started;
    } catch (error) { result.ok=false;result.failedStage=stage;result.error=String(error);if(runtime)result.heapAtFailure=runtime.HEAPU8.byteLength; }
    finally {
      if(runtime){for(const h of outputHandles)if(h)runtime._OrtReleaseTensor(h);if(tensor)runtime._OrtReleaseTensor(tensor);if(runOptions)runtime._OrtReleaseRunOptions(runOptions);if(session)runtime._OrtReleaseSession(session);if(options)runtime._OrtReleaseSessionOptions(options);for(const p of allocations)runtime._free(p);}
    }
    console.log(JSON.stringify(result));
    if(!result.ok)throw new Error(`Beat This inference/parity failed: ${result.error??'output mismatch'}`);
  }
};
