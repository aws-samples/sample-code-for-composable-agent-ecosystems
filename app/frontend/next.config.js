const path = require("path");

// Vendored Amazon DCV Web Client SDK inside the bedrock-agentcore package.
const DCV_SDK = path.resolve(
  __dirname,
  "node_modules/bedrock-agentcore/dist/src/tools/browser/live-view/nice-dcv-web-client-sdk",
);
const appModule = (p) => path.resolve(__dirname, "node_modules", p);

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  outputFileTracingRoot: path.join(__dirname),
  webpack: (config) => {
    config.resolve = config.resolve || {};
    config.resolve.alias = {
      ...(config.resolve.alias || {}),
      // Resolve the ESM-only exports subpath directly to the file (webpack's
      // exports resolution otherwise rejects "./browser/live-view").
      "bedrock-agentcore/browser/live-view": path.join(
        __dirname,
        "node_modules/bedrock-agentcore/dist/src/tools/browser/live-view/index.js",
      ),
      // Point the DCV bare specifiers at the vendored SDK files (the AgentCore
      // BrowserLiveView imports `dcv` + `dcv-ui`).
      dcv: path.join(DCV_SDK, "dcvjs-esm/dcv.js"),
      "dcv-ui": path.join(DCV_SDK, "dcv-ui/dcv-ui.js"),
      // Dedupe: the vendored dcv-ui.js imports these from its own location; force
      // them to resolve from THIS app's node_modules (webpack equivalent of Vite's
      // resolve.dedupe). NOTE: do NOT alias react/react-dom here — Next manages
      // those, and aliasing them breaks Next's internal React.cache during static
      // generation. dcv-ui resolves react up to the app's node_modules anyway.
      "prop-types": appModule("prop-types"),
      "@babel/runtime": appModule("@babel/runtime"),
      "@cloudscape-design/components": appModule("@cloudscape-design/components"),
      "@cloudscape-design/global-styles": appModule("@cloudscape-design/global-styles"),
      "@cloudscape-design/design-tokens": appModule("@cloudscape-design/design-tokens"),
    };
    return config;
  },
};
module.exports = nextConfig;
