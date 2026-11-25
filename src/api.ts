import { invoke, isTauri } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import { open } from '@tauri-apps/plugin-dialog'

const BASE: string = import.meta.env.VITE_NEXORA_API ?? 'http://127.0.0.1:8765'

export type Estado = 'procesando' | 'pendiente' | 'clasificado' | 'corregido' | 'error'

export interface Archivo {
  id: string
  nombre: string
  ruta_actual: string
  carpeta: string | null
  resumen_ejecutivo: string | null
  palabras_clave: string[]
  confianza: number | null
  estado: Estado
  existe_en_disco: boolean
  actualizado_en: string
}
export interface Evento extends Archivo { error: string | null; origen: 'ia' | 'usuario' | null }
export interface Pendiente {
  pendiente_id: string
  archivo: Archivo
  carpeta_sugerida: string
  carpeta_etiqueta: string
  carpeta_existe: boolean
  confianza: number
  alternativas: string[]
  razonamiento: string | null
}
export interface Resumen {
  clasificados_hoy: number
  clasificados_ayer: number
  precision: number | null
  pendientes: number
  errores: number
  en_proceso: number
  total: number
  motor_ia: string
  usando_respaldo: boolean
  vuelve_en: number
}
export interface Propuesta {
  archivo: Archivo | null
  candidatos: Archivo[]
  carpeta: string | null
  carpeta_etiqueta: string | null
  carpeta_existe: boolean
  modelo: string
}
export interface Interpretacion {
  entendida: boolean
  resumen: string
  accion: 'mover' | 'preguntar' | 'no_mover' | 'guia'
  carpeta: string | null
  carpeta_etiqueta: string | null
  carpeta_existe: boolean
  modelo: string
}
export interface Indicacion extends Omit<Interpretacion, 'entendida' | 'modelo'> { id: string; texto: string; activa: boolean; usos: number }
export interface Destino { valor: string; etiqueta: string }
export interface Carpeta { id: string; ruta: string; recursiva: boolean; existe: boolean }
export interface Ajustes { llm: string; respaldo: string | null; umbral_confianza: number; raiz_organizada: string; zonas_busqueda: string[] }
export interface PruebaIA { modelo: string; carpeta: string; confianza: number; resumen: string; segundos: number }

export class MotorApagado extends Error {}

let token: string | null = null

async function obtenerToken(): Promise<string> {
  if (!token) {
    // En el navegador (pnpm dev) no hay acceso nativo: solo la app de escritorio puede leer el token.
    if (!isTauri()) throw new MotorApagado('Abre Nexora como app de escritorio (pnpm tauri:dev) para conectarla con el motor.')
    try {
      token = await invoke<string>('token_escritorio')
    } catch {
      throw new MotorApagado('El motor de Nexora aún no se ha encendido por primera vez.')
    }
  }
  return token
}

async function pedir<T>(ruta: string, opciones: RequestInit = {}, reintento = true): Promise<T> {
  const clave = await obtenerToken()
  let respuesta: Response
  try {
    respuesta = await fetch(`${BASE}/api${ruta}`, {
      ...opciones,
      headers: { ...opciones.headers, Authorization: `Bearer ${clave}` },
    })
  } catch {
    throw new MotorApagado('El motor de Nexora está apagado.')
  }
  if (respuesta.status === 401 && reintento) {
    token = null // el motor pudo generar un token nuevo
    return pedir<T>(ruta, opciones, false)
  }
  if (!respuesta.ok) {
    const cuerpo = await respuesta.json().catch(() => null)
    throw new Error(typeof cuerpo?.detail === 'string' ? cuerpo.detail : `Error ${respuesta.status} del motor.`)
  }
  return (respuesta.status === 204 ? undefined : await respuesta.json()) as T
}

const json = (method: string, cuerpo: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(cuerpo),
})

export const api = {
  resumen: () => pedir<Resumen>('/stats'),
  actividad: (limite = 100) => pedir<Evento[]>(`/activity?limite=${limite}`),
  pendientes: () => pedir<Pendiente[]>('/pending?limite=200'),
  destinos: () => pedir<Destino[]>('/destinations'),
  indicaciones: () => pedir<Indicacion[]>('/rules'),
  interpretarIndicacion: (texto: string) => pedir<Interpretacion>('/rules/interpret', json('POST', { texto })),
  guardarIndicacion: (datos: { texto: string; resumen: string; accion: Indicacion['accion']; carpeta: string | null; crear_carpeta: boolean }) =>
    pedir<Indicacion>('/rules', json('POST', datos)),
  activarIndicacion: (id: string, activa: boolean) => pedir<Indicacion>(`/rules/${id}`, json('PATCH', { activa })),
  borrarIndicacion: (id: string) => pedir<void>(`/rules/${id}`, { method: 'DELETE' }),
  carpetas: () => pedir<Carpeta[]>('/folders'),
  agregarCarpeta: (ruta: string, recursiva: boolean) => pedir<Carpeta>('/folders', json('POST', { ruta, recursiva })),
  quitarCarpeta: (id: string) => pedir<void>(`/folders/${id}`, { method: 'DELETE' }),
  proponerCorreccion: (texto: string, archivo_id: string | null) => pedir<Propuesta>('/corrections', json('POST', { texto, archivo_id })),
  decidir: (archivo_id: string, carpeta_elegida: string, motivo = '') =>
    pedir<{ carpeta_elegida: string; aprendizaje_registrado: boolean }>('/feedback', json('POST', { archivo_id, carpeta_elegida, motivo })),
  reintentar: (id: string) => pedir<unknown>(`/files/${id}/retry`, { method: 'POST' }),
  importar: (rutas: string[]) => pedir<{ aceptados: string[]; omitidos: string[] }>('/files/import', json('POST', { rutas })),
  buscar: (q: string) => pedir<{ resultados: { archivo: Archivo }[] }>(`/files/search?limite=20&q=${encodeURIComponent(q)}`),
  explicar: (id: string, pregunta: string | null) =>
    pedir<{ respuesta: string; modelo: string; texto_truncado: boolean }>(`/files/${id}/explain`, json('POST', { pregunta, detalle: 'breve' })),
  ajustes: () => pedir<Ajustes>('/settings'),
  guardarUmbral: (umbral_confianza: number) => pedir<Ajustes>('/settings', json('PUT', { umbral_confianza })),
  guardarTema: (modo: 'claro' | 'oscuro' | 'auto') => pedir<unknown>('/settings/theme', json('PUT', { modo })),
  probarIA: () => pedir<PruebaIA>('/ai/test', { method: 'POST' }),
}

export async function encenderMotor(): Promise<void> {
  if (isTauri()) await invoke('iniciar_motor')
}

export async function elegirCarpeta(title: string): Promise<string | null> {
  if (!isTauri()) return null
  return open({ directory: true, title })
}

export async function elegirArchivos(): Promise<string[]> {
  if (!isTauri()) return []
  return (await open({ multiple: true, title: 'Elige los archivos que Nexora debe organizar' })) ?? []
}

/** Arrastrar y soltar nativo: entrega la ruta real de cada archivo, necesaria para moverlo en vez de copiarlo. */
export function alSoltarArchivos(encima: (activo: boolean) => void, soltar: (rutas: string[]) => void): () => void {
  if (!isTauri()) return () => {}
  const registro = getCurrentWebview().onDragDropEvent(({ payload }) => {
    encima(payload.type === 'enter' || payload.type === 'over')
    if (payload.type === 'drop') soltar(payload.paths)
  })
  return () => { void registro.then((quitar) => quitar()) }
}
