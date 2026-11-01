/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// The spec folder is the single source for the diagram, the event schema and the example
// streams. The web app imports it directly instead of copying it, so it cannot drift.
const specDir = fileURLToPath(new URL('../spec', import.meta.url))
// Mock mode also reads the lesson chapters and the repo files the inspector shows (src/mock).
const mockDirs = ['../lessons', '../harnesswalk', '../config', '../datasets/_starter'].map((d) => fileURLToPath(new URL(d, import.meta.url)))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.HARNESSWALK_API_URL || 'http://127.0.0.1:8000'
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
        '@spec': specDir,
      },
    },
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      fs: { allow: ['.', specDir, ...mockDirs] },
      // Same-origin in dev: the API's session cookie and SSE work without relying on CORS.
      proxy: {
        '/v1': { target: apiTarget, changeOrigin: false },
        '/healthz': { target: apiTarget, changeOrigin: false },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
      chunkSizeWarningLimit: 900,
    },
    test: {
      environment: 'node',
      include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    },
  }
})
