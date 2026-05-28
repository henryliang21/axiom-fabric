import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build output lands inside the Python package so it ships in the wheel.
// `base: "./"` makes asset URLs relative, so it works served from "/".
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/axiom_fabric_dashboard/static",
    emptyOutDir: true,
  },
  server: {
    // During `npm run dev`, proxy API calls to the running af-dashboard backend.
    proxy: {
      "/api": "http://127.0.0.1:7373",
    },
  },
});
