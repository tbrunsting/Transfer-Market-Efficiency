import { defineConfig } from "astro/config";

// GitHub Pages project site: served from /Transfer-Market-Efficiency/
export default defineConfig({
  site: "https://tbrunsting.github.io",
  base: process.env.PAGES_BASE ?? "/",
  build: { format: "directory" },
});
