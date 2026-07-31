/* Vite config for building the frontend react app: https://vite.dev/config/ */
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
// @ts-expect-error - uidPlugin is a custom plugin
import uidPlugin from './vite-plugin-react-uid'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const radarTarget = env.VITE_RADAR_PROXY_TARGET || 'http://127.0.0.1:5000'
  return ({
  server: {
    host: '::',
    port: 8080,
    proxy: radarTarget
      ? {
          '/api': { target: radarTarget, changeOrigin: true, secure: false },
          '/login': { target: radarTarget, changeOrigin: true, secure: false },
          '/logout': { target: radarTarget, changeOrigin: true, secure: false },
        }
      : undefined,
  },
  build: {
    outDir: mode === 'development' ? 'dev-dist' : 'dist',
    minify: mode !== 'development',
    // lightningcss in every mode so dev/QA catches the same CSS errors as prod
    cssMinify: 'lightningcss',
    sourcemap: mode === 'development',
    rolldownOptions: {
      onwarn(warning, warn) {
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE') {
          return
        }
        warn(warning)
      },
    },
  },
  plugins: [mode === 'development' ? uidPlugin() : undefined, react()].filter(Boolean),
  define: {
    'process.env.NODE_ENV': JSON.stringify(mode ?? process.env.NODE_ENV ?? 'production'),
  },
  resolve: {
    alias: [
      {
        find: '@/lib/pocketbase/client',
        replacement: path.resolve(__dirname, './src/lib/pocketbase/noop.ts'),
      },
      {
        find: '@',
        replacement: path.resolve(__dirname, './src'),
      },
      {
        find: /zod\/v4\/core/,
        replacement: path.resolve(__dirname, 'node_modules', 'zod', 'v4', 'core'),
      }
    ],
  },
  })
})
