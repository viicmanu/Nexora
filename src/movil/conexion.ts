// Conexión del teléfono con el PC vinculado, solo por la red local.
// El tráfico va por Rust (HTTPS con la huella del certificado fijada), nunca por fetch del webview.
import { invoke } from '@tauri-apps/api/core'

export interface Vinculo {
  pc_id: string
  pc_nombre: string
  token: string
  huella: string
  hosts: string[]
  puerto: number
  ip: string // la última que respondió
  dispositivo_id: string
}

export interface ContenidoQR {
  app: 'nexora'
  v: number
  pc: string
  nombre: string
  hosts: string[]
  puerto: number
  huella: string
  codigo: string
}

/** El PC está apagado o no está en esta Wi-Fi. */
export class SinConexion extends Error {}
/** El PC desvinculó este teléfono (401). */
export class Desvinculado extends Error {}

interface Respuesta { estado: number; cuerpo: string }
const esSinConexion = (e: unknown) => String(e).startsWith('sin_conexion')

let alCambiarIp: (ip: string) => void = () => {}
export function avisarCambiosDeIp(f: (ip: string) => void) { alCambiarIp = f }

function interpretar<T>(r: Respuesta): T {
  if (r.estado === 401) throw new Desvinculado('Este teléfono ya no está vinculado al PC.')
  const datos = r.cuerpo ? JSON.parse(r.cuerpo) : undefined
  if (r.estado >= 400) {
    const error = new Error(typeof datos?.detail === 'string' ? datos.detail : `El PC respondió ${r.estado}.`) as Error & { estado?: number; datos?: unknown }
    error.estado = r.estado
    error.datos = datos
    throw error
  }
  return datos as T
}

/** Prueba la última IP, luego las del QR y, si nada responde, busca el PC en la Wi-Fi por mDNS (por si cambió de IP). */
async function conPC<T>(v: Pick<Vinculo, 'ip' | 'hosts' | 'pc_id'>, llamada: (ip: string) => Promise<T>): Promise<T> {
  const probadas = new Set<string>()
  const probar = async (ips: string[]) => {
    for (const ip of ips) {
      if (!ip || probadas.has(ip)) continue
      probadas.add(ip)
      try {
        const r = await llamada(ip)
        if (ip !== v.ip) alCambiarIp(ip)
        return { r }
      } catch (e) {
        if (!esSinConexion(e)) throw e
      }
    }
    return null
  }
  const conocida = await probar([v.ip, ...v.hosts])
  if (conocida) return conocida.r
  const encontradas = await invoke<string[]>('buscar_pc', { pcId: v.pc_id, segundos: 3 }).catch(() => [])
  const nueva = await probar(encontradas)
  if (nueva) return nueva.r
  throw new SinConexion('El PC está desconectado.')
}

export async function pedir<T>(v: Vinculo, ruta: string, metodo = 'GET', cuerpo?: unknown): Promise<T> {
  const r = await conPC(v, (ip) => invoke<Respuesta>('red_peticion', {
    url: `https://${ip}:${v.puerto}/api${ruta}`, metodo, huella: v.huella, token: v.token,
    cuerpo: cuerpo === undefined ? null : JSON.stringify(cuerpo),
  }))
  return interpretar<T>(r)
}

export async function subirFragmento<T>(v: Vinculo, subidaId: string, ruta: string, desde: number, longitud: number): Promise<T> {
  const r = await conPC(v, (ip) => invoke<Respuesta>('red_subir_fragmento', {
    url: `https://${ip}:${v.puerto}/api/uploads/${subidaId}`, huella: v.huella, token: v.token, ruta, desde, longitud,
  }))
  return interpretar<T>(r)
}

export async function descargar(v: Vinculo, archivoId: string, destino: string): Promise<number> {
  return conPC(v, (ip) => invoke<number>('red_descargar', {
    url: `https://${ip}:${v.puerto}/api/files/${archivoId}/download`, huella: v.huella, token: v.token, destino, id: archivoId,
  }))
}

/** Descarga una ruta de la API del PC (p. ej. la actualización de la app) a un archivo local. */
export async function descargarRuta(v: Vinculo, ruta: string, destino: string, id: string): Promise<number> {
  return conPC(v, (ip) => invoke<number>('red_descargar', {
    url: `https://${ip}:${v.puerto}/api${ruta}`, huella: v.huella, token: v.token, destino, id,
  }))
}

export function leerQR(texto: string): ContenidoQR {
  const qr = JSON.parse(texto) as ContenidoQR
  if (qr.app !== 'nexora' || !qr.codigo || !qr.huella || !qr.hosts?.length) throw new Error('No es un código QR de Nexora.')
  return qr
}

/** Canjea el código del QR. Devuelve el vínculo con el token (el PC solo lo entrega una vez). */
export async function vincular(qr: ContenidoQR, nombre: string, plataforma: string | null): Promise<Vinculo> {
  const base = { ip: qr.hosts[0], hosts: qr.hosts, pc_id: qr.pc }
  const r = await conPC(base, (ip) => invoke<Respuesta>('red_peticion', {
    url: `https://${ip}:${qr.puerto}/api/devices/pair`, metodo: 'POST', huella: qr.huella, token: null,
    cuerpo: JSON.stringify({ codigo: qr.codigo, nombre, plataforma }),
  }).then((x) => ({ x, ip })))
  const datos = interpretar<{ token: string; dispositivo_id: string; pc_id: string; pc_nombre: string }>(r.x)
  return {
    pc_id: datos.pc_id || qr.pc, pc_nombre: datos.pc_nombre || qr.nombre, token: datos.token, huella: qr.huella,
    hosts: qr.hosts, puerto: qr.puerto, ip: r.ip, dispositivo_id: datos.dispositivo_id,
  }
}
