// Registro de fallos de la interfaz del teléfono y envío al PC de todos los informes guardados.
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { guardar, leer } from './almacen'
import { pedir, type Vinculo } from './conexion'

interface FalloJS { nombre: string; texto: string }
const VERSION = import.meta.env.VITE_VERSION_MOVIL ?? '0'

function informe(tipo: string, detalle: string) {
  return `tipo: ${tipo}\nfecha: ${new Date().toISOString()}\nnavegador: ${navigator.userAgent}\nversion_app: ${VERSION}\n\n${detalle}`
}

/** Guarda un error de la interfaz para enviarlo al PC después (máximo 20). */
export async function registrarFalloJS(tipo: string, error: unknown, extra = '') {
  const e = error instanceof Error ? `${error.name}: ${error.message}\n${error.stack ?? ''}` : String(error)
  const lista = await leer<FalloJS[]>('fallos_js', []).catch(() => [])
  const nuevo = { nombre: `js-${Date.now()}.txt`, texto: informe(tipo, `${e}\n${extra}`).slice(0, 100_000) }
  await guardar('fallos_js', [...lista, nuevo].slice(-20)).catch(() => {})
}

let instalado = false
export function capturarErroresGlobales() {
  if (instalado) return
  instalado = true
  window.addEventListener('error', (e) => void registrarFalloJS('error_js', e.error ?? e.message, `${e.filename}:${e.lineno}:${e.colno}`))
  window.addEventListener('unhandledrejection', (e) => void registrarFalloJS('promesa_rechazada', e.reason))
}

/** Envía al PC los informes del teléfono (nativos, de Rust y de la interfaz). Devuelve cuántos se enviaron. */
export async function enviarFallos(v: Vinculo): Promise<number> {
  const nativos = await invoke<FalloJS[]>('leer_fallos').catch(() => [] as FalloJS[])
  const js = await leer<FalloJS[]>('fallos_js', [])
  let enviados = 0
  for (const f of [...nativos, ...js]) {
    await pedir(v, '/devices/crash', 'POST', { nombre: f.nombre, texto: f.texto, version: String(VERSION) })
    if (js.some((x) => x.nombre === f.nombre)) await guardar('fallos_js', (await leer<FalloJS[]>('fallos_js', [])).filter((x) => x.nombre !== f.nombre))
    else await invoke('borrar_fallo', { nombre: f.nombre }).catch(() => {})
    enviados++
  }
  return enviados
}

/** Si la interfaz falla al dibujarse, registra el error y muestra una pantalla para recargar en vez de quedar en blanco. */
export class CapturaDeFallos extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) { void registrarFalloJS('error_interfaz', error, info.componentStack ?? '') }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="m-app">
        <section className="m-panel">
          <h2>Algo salió mal</h2>
          <p className="m-texto">Se guardó un informe del error y se enviará a tu PC cuando se conecte.</p>
          <p className="m-error">{this.state.error.message}</p>
          <button className="m-boton principal" onClick={() => location.reload()}>Volver a abrir</button>
        </section>
      </main>
    )
  }
}
