import { beatFeatures, BEAT_SAMPLE_RATE, BEAT_FRAME_RATE, BEAT_MAX_SECONDS } from './beat-frontend.mjs';
import { beatChunkStarts, beatFeatureChunk, decodeBeatFrames, BEAT_BORDER_FRAMES, BEAT_CONTEXT_FRAMES } from './beat-postprocess.mjs';

let busy = false;

/** Analyze one bounded native-rate waveform. runFrames is a parity-test seam. */
export async function analyzeBeatPcm(pcm, { sampleRate = BEAT_SAMPLE_RATE, runFrames } = {}) {
  if (busy) {
    const error = new Error('Beat analysis is busy; retry this request later');
    error.code = 'BEAT_BUSY'; error.retryable = true;
    throw error;
  }
  busy = true;
  const started = performance.now();
  try {
    const { features, frameCount } = beatFeatures(pcm, sampleRate);
    const frontendMs = performance.now() - started;
    const infer = runFrames ?? (await import('./beat-runtime.mjs')).runBeatFrames;
    const starts = beatChunkStarts(frameCount);
    const beat = new Float32Array(frameCount).fill(-1000), downbeat = new Float32Array(frameCount).fill(-1000);
    const seen = new Uint8Array(frameCount);
    let heapCapacityHighWaterBytes = 0;
    for (const start of starts) {
      const output = await infer(beatFeatureChunk(features, frameCount, start));
      if (output.beat?.length !== BEAT_CONTEXT_FRAMES || output.downbeat?.length !== BEAT_CONTEXT_FRAMES || !output.beat.every(Number.isFinite) || !output.downbeat.every(Number.isFinite)) throw new Error('Beat model returned invalid logits');
      heapCapacityHighWaterBytes = Math.max(heapCapacityHighWaterBytes, output.heapCapacityHighWaterBytes ?? 0);
      for (let local = BEAT_BORDER_FRAMES; local < BEAT_CONTEXT_FRAMES - BEAT_BORDER_FRAMES; local++) {
        const frame = start + local;
        if (frame < 0 || frame >= frameCount || seen[frame]) continue;
        beat[frame] = output.beat[local]; downbeat[frame] = output.downbeat[local]; seen[frame] = 1;
      }
    }
    if (seen.some(value => value === 0)) throw new Error('Beat chunk coverage is incomplete');
    const durationSeconds = pcm.length / sampleRate;
    return {
      ...decodeBeatFrames(beat, downbeat, durationSeconds), durationSeconds,
      provenance: { model: 'Beat This small0', checkpointSha256: '6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b', onnxSha256: '2c61c460654723857fc8dface211d133e6c983efec4c5e91b576bfca746182f1', runtime: runFrames ? 'caller-provided parity executor' : 'onnxruntime-wasm 1.22.0-dev.20250409-89f8206ba4 / workerd', sampleRate, frameRate: BEAT_FRAME_RATE, contextFrames: BEAT_CONTEXT_FRAMES, borderFrames: BEAT_BORDER_FRAMES, overlap: 'keep_first', decoder: 'official-minimal', maxDurationSeconds: BEAT_MAX_SECONDS, shortWindowPadding: 'zero to 250 frames' },
      warnings: [ ...(pcm.every(value => value === 0) ? ['Input PCM is digital silence; timing predictions are not musical evidence.'] : []), 'Five-second model context; beats and downbeats are model estimates, not verified musical timing.' ],
      diagnostics: { frameCount, chunks: starts.length, frontendMs, totalMs: performance.now() - started, wasmCapacityBytes: heapCapacityHighWaterBytes || null, memoryNote: 'WASM capacity is not whole-isolate peak memory' },
    };
  } finally { busy = false; }
}
