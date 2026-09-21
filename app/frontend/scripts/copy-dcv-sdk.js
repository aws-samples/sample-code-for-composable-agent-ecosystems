// Copies the vendored Amazon DCV Web Client SDK runtime (dcvjs-esm + dcv-ui) out
// of the bedrock-agentcore package into the BUILD OUTPUT (out/nice-dcv-web-client-sdk/)
// — NOT into public/ and NOT committed. The DCV viewer loads its workers + WASM
// decoders at runtime from the hardcoded baseUrl /nice-dcv-web-client-sdk/dcvjs-esm,
// so these must be present at the served site root. Runs as an npm "postbuild"
// step (after `next build`, which produces out/ for output:export).
// See docs/features/browser-tool.md + AgentCore BROWSER_LIVE_VIEW doc.
const fs = require("fs");
const path = require("path");

const sdkDir = path.resolve(
  __dirname, "..",
  "node_modules/bedrock-agentcore/dist/src/tools/browser/live-view/nice-dcv-web-client-sdk",
);
const outDir = path.resolve(__dirname, "..", "out");
const destRoot = path.join(outDir, "nice-dcv-web-client-sdk");

if (!fs.existsSync(outDir)) {
  console.warn(`[copy-dcv-sdk] build output ${outDir} not found — skipping (run after 'next build').`);
  process.exit(0);
}
if (!fs.existsSync(sdkDir)) {
  console.warn(`[copy-dcv-sdk] vendored DCV SDK not found at ${sdkDir} — live viewer will be unavailable.`);
  process.exit(0);
}

fs.rmSync(destRoot, { recursive: true, force: true });
for (const sub of ["dcvjs-esm", "dcv-ui"]) {
  const src = path.join(sdkDir, sub);
  const dest = path.join(destRoot, sub);
  if (fs.existsSync(src)) {
    fs.cpSync(src, dest, { recursive: true });
    console.log(`[copy-dcv-sdk] copied ${sub} -> out/nice-dcv-web-client-sdk/${sub}`);
  }
}
