import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// Host expuesto por `tauri android/ios dev` para dispositivos móviles.
const host = process.env.TAURI_DEV_HOST

// Configuración de Vite para Nexora (Tauri + React) — https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // Evita que Vite oculte los errores de compilación de Rust.
  clearScreen: false,
  server: {
    // Tauri espera un puerto fijo; si está ocupado, falla en lugar de cambiarlo.
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: 'ws', host, port: 1421 } : undefined,
    watch: {
      // No vigilar el backend de Rust desde Vite.
      ignored: ['**/src-tauri/**'],
    },
  },
  // Variables de entorno con estos prefijos quedan disponibles en import.meta.env.
  envPrefix: ['VITE_', 'TAURI_ENV_*'],
  build: {
    // WebView2 (Windows) es Chromium; WebKit en macOS/Linux.
    target: process.env.TAURI_ENV_PLATFORM === 'windows' ? 'chrome105' : 'safari13',
    minify: !process.env.TAURI_ENV_DEBUG ? 'oxc' : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
  },
})
