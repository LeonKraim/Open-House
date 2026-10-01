import { defineConfig } from "vite";

/**
 * The panel is built as a single ES module, not as an application.
 *
 * Home Assistant's `panel_custom` loads one module URL and expects it to define
 * a custom element on import; there is no second request it will make, so the
 * bundle must be self-contained. `inlineDynamicImports` collapses any dynamic
 * import into the one file, and claiming no external dependency makes Rollup
 * bundle Lit rather than leaving a bare specifier the browser cannot resolve.
 *
 * The output name is fixed rather than hashed: the integration registers
 * `dist/open-house-panel.js` by that exact path, and cache-busting belongs to
 * the URL the integration builds (`?v=<version>`) rather than to a filename
 * nobody can predict at registration time.
 */
export default defineConfig({
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    lib: {
      entry: "src/main.ts",
      formats: ["es"],
      fileName: () => "open-house-panel.js",
    },
    rollupOptions: {
      external: [],
      output: {
        inlineDynamicImports: true,
      },
    },
  },
});
