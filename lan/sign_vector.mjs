// Music Lens LAN API v0.6 reference signer (Node, no deps).
// Prints the fixed signing vector so it can be compared with sign.py (Python).
// Usage: node sign_vector.mjs            -> JSON vector
//        node sign_vector.mjs --check F  -> compare with vectors.json file F, exit 1 on mismatch
import crypto from 'node:crypto';
import fs from 'node:fs';

export const VECTOR = {
  key_hex: '000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f',
  client: 'claude-window',
  time: 1793000000,
  nonce: '0123456789abcdef0123456789abcdef',
  name: '테스트 곡',
  post_body_hex: '52494646',
};
const EMPTY_SHA256 = crypto.createHash('sha256').update(Buffer.alloc(0)).digest('hex');

export function canonical(client, method, target, contentType, contentLength, time, nonce, bodySha256) {
  const fields = [client, method.toUpperCase(), target, contentType, String(contentLength), String(time), nonce, bodySha256];
  if (fields.some((f) => f.includes('\n'))) throw new Error('canonical field contains LF');
  return Buffer.from(fields.join('\n'), 'utf8');
}

export function signature(keyHex, canonicalBytes) {
  return crypto.createHmac('sha256', Buffer.from(keyHex, 'hex')).update(canonicalBytes).digest('hex');
}

export function vectorValues(v = VECTOR) {
  const target = '/analyze?language=ko&emotion_mode=voice&name=' + encodeURIComponent(v.name);
  const body = Buffer.from(v.post_body_hex, 'hex');
  const bodySha = crypto.createHash('sha256').update(body).digest('hex');
  const cPost = canonical(v.client, 'POST', target, 'application/octet-stream', body.length, v.time, v.nonce, bodySha);
  const cGet = canonical(v.client, 'GET', '/status', '', 0, v.time, v.nonce, EMPTY_SHA256);
  return {
    post_target: target,
    post_canonical_hex: cPost.toString('hex'),
    post_sig: signature(v.key_hex, cPost),
    get_canonical_hex: cGet.toString('hex'),
    get_sig: signature(v.key_hex, cGet),
  };
}

const out = vectorValues();
const i = process.argv.indexOf('--check');
if (i > 0) {
  const want = JSON.parse(fs.readFileSync(process.argv[i + 1], 'utf8'));
  const bad = Object.keys(out).filter((k) => want[k] !== out[k]);
  process.stdout.write(JSON.stringify({ ok: bad.length === 0, mismatched: bad }) + '\n');
  process.exit(bad.length ? 1 : 0);
}
process.stdout.write(JSON.stringify(out) + '\n');
