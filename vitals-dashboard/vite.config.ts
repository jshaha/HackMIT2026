import path from "path"
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import tailwindcss from "@tailwindcss/vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    watch: {
      // The pipeline rewrites these runtime files every ~2s; the app polls them
      // over HTTP. Don't let Vite full-reload the page when they change.
      ignored: [
        "**/public/reading.json",
        "**/public/preview.jpg",
        "**/public/agenda_state.json",
        "**/public/visit_summary.json",
        "**/public/transcript.json",
        "**/public/soa_state.json",
        "**/public/edc_forms.json",
        "**/public/oversight_state.json",
        "**/public/monitoring_report.json",
      ],
    },
  },
})

