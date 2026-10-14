// Beat This / torchaudio LogMelSpect: 22050 Hz, periodic Hann, centered reflect
// STFT, magnitude normalized by sqrt(1024), Slaney mel without area norm.
import { hann, filters } from './beat-frontend-constants.mjs';
import { spectralDescriptors } from '../../public/analysis/spectral-descriptors.mjs';
export const BEAT_SAMPLE_RATE = 22050;
export const BEAT_FRAME_RATE = 50;
export const BEAT_MAX_SECONDS = 30;
export const BEAT_FFT_SIZE = 1024;
export const BEAT_HOP_SIZE = 441;
const SIZE = BEAT_FFT_SIZE, HOP = BEAT_HOP_SIZE, BINS = 128, half = SIZE / 2;
const f32 = Math.fround;
const reverse = Uint16Array.from({ length: SIZE }, (_, i) => {
  let result = 0;
  for (let bit = 0; bit < 10; bit++) { result = (result << 1) | (i & 1); i >>= 1; }
  return result;
});

function magnitude(pcm, center, real, imag, output) {
  let energy = 0;
  for (let i = 0; i < SIZE; i++) {
    let sample = center + i - half;
    if (sample < 0) sample = -sample;
    if (sample >= pcm.length) sample = 2 * pcm.length - 2 - sample;
    energy += pcm[sample] * pcm[sample];
    real[reverse[i]] = f32(pcm[sample] * hann[i]);
    imag[reverse[i]] = 0;
  }
  for (let length = 2; length <= SIZE; length *= 2) {
    const width = length / 2;
    for (let offset = 0; offset < SIZE; offset += length) {
      for (let i = 0; i < width; i++) {
        const angle = -2 * Math.PI * i / length, wr = Math.cos(angle), wi = Math.sin(angle);
        const left = offset + i, right = left + width;
        const tr = wr * real[right] - wi * imag[right], ti = wr * imag[right] + wi * real[right];
        real[right] = real[left] - tr; imag[right] = imag[left] - ti;
        real[left] += tr; imag[left] += ti;
      }
    }
  }
  for (let i = 0; i <= half; i++) output[i] = f32(Math.hypot(real[i], imag[i]) / 32);
  return Math.sqrt(energy / SIZE);
}

export function beatFeatures(pcm, sampleRate = BEAT_SAMPLE_RATE) {
  if (!(pcm instanceof Float32Array) || sampleRate !== BEAT_SAMPLE_RATE) throw new TypeError('Beat analysis requires mono Float32 PCM at exactly 22050 Hz');
  if (pcm.length < BEAT_SAMPLE_RATE || pcm.length > BEAT_SAMPLE_RATE * BEAT_MAX_SECONDS) throw new RangeError(`Beat analysis accepts 1–${BEAT_MAX_SECONDS} seconds per request`);
  if (!pcm.every(value => Number.isFinite(value) && value >= -1 && value <= 1)) throw new RangeError('PCM must contain finite samples within [-1, 1]');
  const frameCount = Math.floor(pcm.length / HOP) + 1;
  const features = new Float32Array(frameCount * BINS);
  const spectralFrames = [];
  const real = new Float64Array(SIZE), imag = new Float64Array(SIZE), spectrum = new Float32Array(half + 1);
  for (let frame = 0; frame < frameCount; frame++) {
    const rms = magnitude(pcm, frame * HOP, real, imag, spectrum);
    // Reuse the pre-mel FFT, not compressed log-mel features. The model's
    // /sqrt(1024) magnitude becomes /1024 for shared descriptor floors.
    spectralFrames.push({ time: frame * HOP / sampleRate, rms,
      ...spectralDescriptors(spectrum.subarray(1, half), sampleRate, SIZE, { magnitudeScale: 1 / 32 }) });
    for (let band = 0; band < BINS; band++) {
      let sum = 0;
      for (const [bin, weight] of filters[band]) sum += spectrum[bin] * weight;
      features[frame * BINS + band] = Math.log1p(1000 * sum);
    }
  }
  return { features, frameCount, spectralFrames };
}
