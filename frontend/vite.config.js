// Archival Vite configuration: production/development proxy mappings omitted.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
export default defineConfig({base: "./", plugins: [react()], build: {outDir: "../dist", emptyOutDir: true}});
