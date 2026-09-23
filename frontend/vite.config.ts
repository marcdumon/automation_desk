import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// CLAUDE> built into the Python package so FastAPI serves it; proxied in development so the browser sees one origin
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../src/llm_automation/static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
})
