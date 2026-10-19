import vinext from "vinext";
import { defineConfig } from "vite";

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

// NOTE (fixed): this file previously imported "./.openai/hosting.json" and
// "./build/sites-vite-plugin" and wired up Cloudflare D1/R2 bindings named
// "site-creator-d1" / "site-creator-r2". None of that exists in this repo
// and none of it belongs here — Atlas is a single-user, local-first app
// (see ARCHITECTURE.md): all persistent state lives in the browser's
// IndexedDB via app/lib/storage.ts, there is no D1 database, no R2 bucket,
// and no hosted backend. That dead scaffolding was left over from an
// unrelated site-generator template and made `npm run dev:hmr` fail
// immediately on import. It has been removed rather than patched.
export default defineConfig(() => {
  return {
    // The terminal does not need Vite's pre-bundling pass. Disabling
    // automatic discovery keeps local development reliable in locked-down
    // environments where the esbuild optimizer cannot spawn a long-lived
    // IPC helper. Production builds are still fully bundled by Vinext.
    optimizeDeps: { noDiscovery: true, include: [] },
    server: isCodexSeatbeltSandbox
      ? { watch: { useFsEvents: false, usePolling: true } }
      : undefined,
    plugins: [vinext()],
  };
});
