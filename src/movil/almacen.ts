// Estado persistente del teléfono: vínculo, cola de subidas y decisiones, y caché de lo organizado.
// Sobrevive a cerrar la app y a reiniciar el teléfono (plugin store + archivos en la carpeta de la app).
import { invoke } from '@tauri-apps/api/core'
import { appDataDir, join } from '@tauri-apps/api/path'
import { BaseDirectory, exists, mkdir, readFile, remove } from '@tauri-apps/plugin-fs'
import { load, type Store } from '@tauri-apps/plugin-store'
import { Desvinculado, SinConexion, descargar, pedir, subirFragmento, type Vinculo } from './conexion'
import type { Archivo, Destino, Pendiente } from '../api'

export type EstadoCola = 'espera' | 'enviando' | 'recibido' | 'organizado' | 'bandeja' | 'error'
export interface ElementoCola {
  id: string // cliente_id: reenviar el mismo elemento no duplica en el PC
  nombre: string
  ruta: string // copia local dentro de la app
  tamano: number
  sha256: string
  estado: EstadoCola
  enviados: number
  subida_id?: string
  archivo_id?: string
  error?: string
  intentos: number
  proximo: number // marca de tiempo del próximo intento (backoff)
}
export interface Decision { id: string; tipo: 'decidir' | 'ignorar'; archivo_id: string; pendiente_id: string; carpeta?: string; nombre: string }
export interface Organizado extends Archivo { carpeta_etiqueta: string | null; tipo: string; clasificado_en: string | null }

const FRAGMENTO = 4 * 1024 * 1024
let store: Store | null = null
const tienda = async () => (store ??= await load('movil.json', { autoSave: false, defaults: {} }))

export async function leer<T>(clave: string, porDefecto: T): Promise<T> {
  return ((await (await tienda()).get<T>(clave)) ?? porDefecto)
}
export async function guardar(clave: string, valor: unknown) {
  const s = await tienda()
  await s.set(clave, valor)
  await s.save()
}

/** Al desvincular se borra todo: token, caché, cola y sus copias de archivos. */
export async function olvidarTodo() {
  const s = await tienda()
  await s.clear()
  await s.save()
  if (await exists('cola', { baseDir: BaseDirectory.AppData })) await remove('cola', { baseDir: BaseDirectory.AppData, recursive: true })
}

const nuevoId = () => crypto.randomUUID()
const nombreDe = (ruta: string) => decodeURIComponent(ruta.split(/[\\/]/).pop() ?? 'archivo').replace(/[<>:"|?*]/g, '_') || 'archivo'

/** Copia el archivo elegido a la carpeta de la app: así la cola sigue aunque se cierre la app o se reinicie el teléfono. */
export async function encolar(origen: string | File, maxBytes: number): Promise<ElementoCola> {
  const id = nuevoId()
  let ruta: string
  if (typeof origen === 'string') {
    // Ruta normal: copia directa en Rust. URI content:// de Android: se lee con el plugin y se escribe en binario.
    ruta = await invoke<string>('copiar_a_cola', { origen, id }).catch(async () =>
      invoke<string>('escribir_cola', await readFile(origen), { headers: { 'x-id': id } }))
  } else {
    if (origen.size > maxBytes) throw new Error(`Supera el máximo de ${Math.round(maxBytes / 1048576)} MB que acepta el PC.`)
    ruta = await invoke<string>('escribir_cola', new Uint8Array(await origen.arrayBuffer()), { headers: { 'x-id': id } })
  }
  const [sha256, tamano] = await invoke<[string, number]>('archivo_sha256', { ruta })
  if (tamano > maxBytes) {
    await remove(`cola/${id}`, { baseDir: BaseDirectory.AppData }).catch(() => {})
    throw new Error(`Supera el máximo de ${Math.round(maxBytes / 1048576)} MB que acepta el PC.`)
  }
  const elemento: ElementoCola = {
    id, nombre: typeof origen === 'string' ? nombreDe(origen) : origen.name || 'foto.jpg', ruta, tamano, sha256,
    estado: 'espera', enviados: 0, intentos: 0, proximo: 0,
  }
  await guardar('cola', [...(await leer<ElementoCola[]>('cola', [])), elemento])
  return elemento
}

export async function quitarDeCola(v: Vinculo | null, id: string) {
  const cola = await leer<ElementoCola[]>('cola', [])
  const e = cola.find((x) => x.id === id)
  if (e?.subida_id && v && e.estado === 'enviando') await pedir(v, `/uploads/${e.subida_id}`, 'DELETE').catch(() => {})
  await remove(`cola/${id}`, { baseDir: BaseDirectory.AppData }).catch(() => {})
  await guardar('cola', cola.filter((x) => x.id !== id))
}

interface Subida { id: string; bytes_recibidos: number; estado: string; archivo: Archivo | null }

/** Envía la cola en orden. Reanuda desde lo que el PC ya tiene y espera cada vez más tras un fallo. */
export async function procesarCola(v: Vinculo, avisar: () => void): Promise<void> {
  let cola = await leer<ElementoCola[]>('cola', [])
  const actualizar = async (e: ElementoCola) => {
    cola = (await leer<ElementoCola[]>('cola', [])).map((x) => (x.id === e.id ? e : x))
    await guardar('cola', cola)
    avisar()
  }
  for (const original of cola) {
    const e = { ...original }
    if (!['espera', 'enviando', 'error', 'recibido'].includes(e.estado) || e.proximo > Date.now()) continue
    try {
      if (e.estado !== 'recibido') {
        const s = await pedir<Subida>(v, '/uploads', 'POST', { cliente_id: e.id, nombre: e.nombre, tamano: e.tamano, sha256: e.sha256 })
        e.subida_id = s.id
        e.enviados = s.bytes_recibidos
        e.estado = 'enviando'
        e.error = undefined
        await actualizar(e)
        if (s.estado === 'recibiendo') {
          while (e.enviados < e.tamano) {
            const r = await subirFragmento<Subida>(v, s.id, e.ruta, e.enviados, Math.min(FRAGMENTO, e.tamano - e.enviados)).catch((err) => {
              const datos = (err as { datos?: { bytes_recibidos?: number } }).datos
              if (typeof datos?.bytes_recibidos === 'number') return { bytes_recibidos: datos.bytes_recibidos } as Subida // seguir desde lo recibido
              throw err
            })
            e.enviados = r.bytes_recibidos
            await actualizar(e)
          }
          await pedir<Subida>(v, `/uploads/${s.id}/complete`, 'POST')
        }
        e.estado = 'recibido'
        e.intentos = 0
        await actualizar(e)
        await remove(`cola/${e.id}`, { baseDir: BaseDirectory.AppData }).catch(() => {}) // ya está a salvo en el PC
      }
      const s = await pedir<Subida>(v, `/uploads/${e.subida_id}`)
      if (s.estado === 'organizada' && s.archivo) {
        e.archivo_id = s.archivo.id
        e.estado = s.archivo.estado === 'pendiente' ? 'bandeja' : 'organizado'
        await actualizar(e)
      } else if (s.estado === 'error') {
        throw new Error('El PC no pudo organizarlo.')
      }
    } catch (err) {
      if (err instanceof Desvinculado) throw err
      e.intentos += 1
      e.estado = e.estado === 'recibido' ? 'recibido' : err instanceof SinConexion ? 'espera' : 'error'
      e.error = err instanceof SinConexion ? undefined : String((err as Error).message ?? err)
      e.proximo = Date.now() + Math.min(5000 * 2 ** Math.min(e.intentos, 7), 10 * 60_000)
      await actualizar(e)
      if (err instanceof SinConexion) return // sin PC no tiene sentido seguir con el resto
    }
  }
}

/** Envía las decisiones de la Bandeja tomadas sin conexión. */
export async function procesarDecisiones(v: Vinculo): Promise<void> {
  for (const d of await leer<Decision[]>('decisiones', [])) {
    try {
      if (d.tipo === 'decidir') await pedir(v, '/feedback', 'POST', { archivo_id: d.archivo_id, carpeta_elegida: d.carpeta })
      else await pedir(v, `/pending/${d.pendiente_id}/ignore`, 'POST')
    } catch (err) {
      if (err instanceof SinConexion || err instanceof Desvinculado) throw err
      // ya resuelta en el PC u otro error definitivo: se descarta
    }
    await guardar('decisiones', (await leer<Decision[]>('decisiones', [])).filter((x) => x.id !== d.id))
  }
}

export async function decidir(d: Omit<Decision, 'id'>) {
  await guardar('decisiones', [...(await leer<Decision[]>('decisiones', [])), { ...d, id: nuevoId() }])
}

/** Sincroniza lo organizado (incremental) y la Bandeja. Devuelve las dudas nuevas para notificarlas. */
export async function sincronizar(v: Vinculo): Promise<Pendiente[]> {
  const desde = await leer<string | null>('organizados_hasta', null)
  const r = await pedir<{ items: Organizado[]; ids: string[]; hasta: string | null }>(
    v, `/files/organized?limite=2000${desde ? `&since=${encodeURIComponent(desde)}` : ''}`,
  )
  const vigentes = new Set(r.ids)
  const cache = new Map((await leer<Organizado[]>('organizados', [])).filter((o) => vigentes.has(o.id)).map((o) => [o.id, o]))
  for (const item of r.items) cache.set(item.id, item)
  await guardar('organizados', [...cache.values()])
  await guardar('organizados_hasta', r.hasta)
  const pendientes = await pedir<Pendiente[]>(v, '/pending?limite=200')
  await guardar('pendientes', pendientes)
  await guardar('destinos', await pedir<Destino[]>(v, '/destinations'))
  await guardar('ultima_sync', new Date().toISOString())
  const avisadas = new Set(await leer<string[]>('avisadas', []))
  const nuevas = pendientes.filter((p) => !avisadas.has(p.pendiente_id))
  await guardar('avisadas', pendientes.map((p) => p.pendiente_id))
  return nuevas
}

export async function bajar(v: Vinculo, o: Organizado): Promise<string> {
  await mkdir('descargas', { baseDir: BaseDirectory.AppData, recursive: true })
  const destino = await join(await appDataDir(), 'descargas', o.nombre)
  await descargar(v, o.id, destino)
  return destino
}
