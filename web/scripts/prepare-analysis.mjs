import { build } from "esbuild";
import { copyFile, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { VOCAL_MODEL_SHA256 } from "../analysis/vocal-notes.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const target = path.join(root, "public/models");
const manifest = [];
async function copyAssets(pkg, subdir, destination, pattern) {
  const source = path.join(root, "node_modules", pkg, subdir);
  const dest = path.join(target, destination);
  await mkdir(dest, { recursive: true });
  for (const name of await readdir(source)) {
    if (!pattern.test(name)) continue;
    const bytes = await readFile(path.join(source, name));
    await copyFile(path.join(source, name), path.join(dest, name));
    manifest.push({ path: `/models/${destination}/${name}`, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") });
  }
}
await copyAssets("@spotify/basic-pitch", "model", "basic-pitch", /\.(json|bin)$/);
await copyAssets("@tensorflow/tfjs-backend-wasm", "dist", "tfjs", /\.wasm$/);
await copyAssets("onnxruntime-web", "dist", "ort", /^ort-wasm-simd-threaded(?:\.jsep)?\.(wasm|mjs)$/);
try {
  const bytes = await readFile(path.join(target, "some/model.onnx"));
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  if (sha256 !== VOCAL_MODEL_SHA256) throw new Error("SOME model identity mismatch. Re-run scripts/prepare-vocal-model.py.");
  manifest.push({ path: "/models/some/model.onnx", bytes: bytes.length, sha256 });
} catch (e) {
  if (e.code !== "ENOENT") throw e;
  console.log("Optional solo-vocal model unavailable. Prepare it with scripts/prepare-vocal-model.py before choosing that mode.");
}
await build({ entryPoints: [path.join(root, "analysis/precision-worker.mjs")], outfile: path.join(root, "public/precision-worker.mjs"), bundle: true, format: "esm", platform: "browser", target: "es2022", minify: true, legalComments: "linked", external: ["/precision-rhythm.mjs"], define: { "process.env.NODE_ENV": '"production"' } });
await writeFile(path.join(target, "manifest.json"), JSON.stringify({ runtimes: { basicPitch: "1.0.1", tfjs: "3.21.0", transformers: "3.8.1" }, assets: manifest }, null, 2) + "\n");
console.log(`Prepared browser analysis worker and ${manifest.length} pinned runtime/model assets (${manifest.reduce((n, a) => n + a.bytes, 0)} bytes).`);
