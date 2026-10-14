import { analyzeAudio } from './audio-engine.mjs';

// One analysis per worker. The UI cancels by terminating this worker.
self.onmessage = async ({ data }) => {
  const { id, samples, sampleRate } = data;
  try {
    const result = await analyzeAudio(samples, sampleRate, {
      onProgress: (progress, label) => self.postMessage({ id, type: 'progress', progress, label }),
    });
    self.postMessage({ id, type: 'result', result });
  } catch (error) {
    self.postMessage({ id, type: 'error', error: error instanceof Error ? error.message : 'Local audio analysis failed.' });
  }
};
