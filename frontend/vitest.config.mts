import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// .mts, not .ts: this file uses ESM syntax and the package is CommonJS, so a
// .ts extension makes Vite's native config loader warn.
export default defineConfig({
  plugins: [react()],
  resolve: {
    // Resolves the "@/*" alias from tsconfig.json rather than duplicating it
    // here, so the two cannot drift apart. Native since Vite 8, replacing the
    // vite-tsconfig-paths plugin.
    tsconfigPaths: true,
  },
  test: {
    // jsdom over happy-dom: these tests exercise focus management,
    // document.activeElement, and keyboard events, where jsdom's fidelity is
    // the safer default.
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // Globals stay off; test files import describe/it/expect explicitly, which
    // keeps `tsc --noEmit` honest without a global type shim.
    globals: false,
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
    // NEXT_PUBLIC_* are inlined at build time and read at module load, so
    // api.ts captures NEXT_PUBLIC_API_URL the moment it is imported. Without
    // a value here every request short-circuits on "not configured" before
    // any fetch is attempted.
    env: {
      NEXT_PUBLIC_API_URL: "http://api.test/api/v1",
      NEXT_PUBLIC_SUPABASE_URL: "https://test-project.supabase.co",
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "test-publishable-key",
    },
  },
});
