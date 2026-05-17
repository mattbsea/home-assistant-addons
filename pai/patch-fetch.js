// Prefixes absolute "/api/..." and "/pai-logo..." references in the PAI
// Observatory source with the Home Assistant ingress base path.
//
// Next.js `basePath` rewrites framework URLs (assets, routes, RSC payloads)
// but not hand-written fetch() calls, so those are patched here before the
// dashboard is rebuilt.

import { readdirSync, statSync, readFileSync, writeFileSync } from "fs";
import { join } from "path";

const [dir, basePath] = process.argv.slice(2);
if (!basePath) process.exit(0);

let patchedCount = 0;

function walk(directory) {
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      walk(path);
      continue;
    }
    if (!/\.(ts|tsx|js|jsx)$/.test(path)) continue;
    const original = readFileSync(path, "utf8");
    const patched = original
      .replace(/(["'`])\/api\//g, `$1${basePath}/api/`)
      .replace(/(["'`])\/pai-logo/g, `$1${basePath}/pai-logo`);
    if (patched !== original) {
      writeFileSync(path, patched);
      patchedCount++;
    }
  }
}

walk(dir);
console.log(`patch-fetch: prefixed ${patchedCount} file(s) with ${basePath}`);
