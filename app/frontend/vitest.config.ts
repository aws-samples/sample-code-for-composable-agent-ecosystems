import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));

// Vitest config for frontend unit tests. jsdom environment for React component tests,
// with the same `@/` -> src alias the app + tsconfig use. Tests live next to source as
// *.test.ts(x) or under src/**/__tests__/.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(dir, "src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}", "src/**/__tests__/**/*.{ts,tsx}"],
    // Exclude the Next build output + node_modules from collection.
    exclude: ["node_modules", ".next", "out"],
  },
});
