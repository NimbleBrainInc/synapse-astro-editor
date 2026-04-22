import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { synapseVite } from "@nimblebrain/synapse/vite";

export default defineConfig({
  plugins: [
    react(),
    viteSingleFile(),
    synapseVite(),
    // Reads ../manifest.json automatically and starts the MCP server in stdio
    // mode. Preview host is served at /__preview.
  ],
  build: {
    outDir: "dist",
    assetsInlineLimit: Infinity,
  },
});
