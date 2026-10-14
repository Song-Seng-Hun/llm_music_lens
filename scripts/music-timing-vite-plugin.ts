import path from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";
import type { Plugin } from "vite";

// Cloudflare's Vite plugin emits .wasm as CompiledWasm and .bin as Data.
// No eval, runtime compilation, browser Worker or remote inference is used.
export function musicTimingAssets(): Plugin {
  const virtual = "virtual:music-lens-beat-assets";
  const resolved = "\0" + virtual;
  const dir = fileURLToPath(new URL("../.sites-runtime/server-beat/", import.meta.url));
  return {
    name: "music-lens-server-beat-assets", enforce: "pre",
    resolveId(source) { if (source === virtual) return resolved; },
    load(id) {
      if (id !== resolved) return;
      const imports = [["factory", "ort-factory.mjs"], ["compiled", "ort.wasm"], ["modelBytes", "beat-small-250.bin"]];
      for (const [, file] of imports) {
        const full = path.join(dir, file);
        if (!existsSync(full)) throw new Error(`Missing server timing asset ${file}; run the server-beat preparation step before building.`);
        this.addWatchFile(full);
      }
      return imports.map(([name, file]) => `import ${name} from ${JSON.stringify(path.join(dir, file).replaceAll("\\", "/"))};`).join("\n")
        + "\nexport default { factory, compiled, modelBytes };\n";
    },
  };
}
