# Nexora

Aplicación de escritorio construida con **Tauri 2 + React 19**. El frontend usa Vite 8, TypeScript y Tailwind CSS v4; el backend nativo está en Rust (`src-tauri/`).

## Comandos

- `pnpm install` — instala las dependencias de JavaScript.
- `pnpm tauri:dev` — abre la app con recarga en caliente y enciende el backend si está apagado (registro en `%APPDATA%/Nexora/motor.log`).
- `pnpm tauri:build` — genera el instalador en `src-tauri/target/release/bundle/`.
- `pnpm dev` — solo el frontend en el navegador (`http://localhost:1420`), sin APIs nativas.
- `pnpm build` — verifica tipos y compila el frontend a `dist/`.

## Requisitos (Windows)

- Node.js 22+ y pnpm.
- Rust estable (`rustup`, toolchain `x86_64-pc-windows-msvc`).
- Microsoft C++ Build Tools con la carga de trabajo "Desarrollo para el escritorio con C++".
- WebView2 (incluido en Windows 10/11).

## Estructura

- `src/main.tsx` — punto de entrada de React; importa `src/index.css` y monta `src/App.tsx`.
- `src/App.tsx` — interfaz (Dashboard, Bandeja, Carpetas, Historial, Configuración).
- `src/api.ts` — cliente de la API del backend.
- `src/index.css` — estilos globales e importación de Tailwind CSS v4.
- `index.html` — documento base con el `#root`.
- `vite.config.ts` — Vite con React, Tailwind y los ajustes que Tauri necesita (puerto fijo 1420, alias `@` → `src`).
- `src-tauri/tauri.conf.json` — configuración de Tauri: nombre (Nexora), identificador `com.nexora.app`, ventana e instalador.
- `src-tauri/src/lib.rs` — lógica de Rust y comandos expuestos a React (`#[tauri::command]`).
- `src-tauri/src/main.rs` — ejecutable que llama a `nexora_lib::run()`.
- `src-tauri/capabilities/` — permisos que el frontend tiene sobre las APIs nativas.
- `src-tauri/icons/` — iconos de la app (regenerar con `pnpm tauri icon ruta/al/icono.png`, ideal 1024×1024).

## Llamar a Rust desde React

```ts
import { invoke } from '@tauri-apps/api/core'
const token = await invoke<string>('token_escritorio')
```

## Estilos

Tailwind CSS v4 se carga con `@import 'tailwindcss';` en `src/index.css`; no hace falta `tailwind.config` ni PostCSS. Los estilos globales de elementos (`body`, `button`, etc.) deben ir dentro de `@layer base { ... }` para que las utilidades de Tailwind puedan sobrescribirlos.

## Backend (Python / FastAPI)

`backend/` contiene el servicio local que vigila carpetas, clasifica archivos con IA y expone la API REST en `http://127.0.0.1:8765`. Módulos: `core_ia` (adaptadores LLM, clasificación, búsqueda, aprendizaje), `file_watcher`, `api_desktop_mobile` y `security`. Detalles, esquema y endpoints en [backend/README.md](backend/README.md).

- `backend\.venv\Scripts\python -m nexora` — arranca el backend (lee `backend/.env`).
- `backend\.venv\Scripts\python -m pytest` — pruebas del backend (sin red).
