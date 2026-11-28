import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { appCacheDir, join } from '@tauri-apps/api/path'
import { open } from '@tauri-apps/plugin-dialog'
import { isPermissionGranted, requestPermission, sendNotification } from '@tauri-apps/plugin-notification'
import { openPath } from '@tauri-apps/plugin-opener'
import {
  avisarCambiosDeIp, descargarRuta, Desvinculado, leerQR, pedir, SinConexion, vincular, type ContenidoQR, type Vinculo,
} from './conexion'
import {
  bajar, decidir, encolar, guardar, leer, olvidarTodo, procesarCola, procesarDecisiones, quitarDeCola, sincronizar,
  type Decision, type ElementoCola, type Organizado,
} from './almacen'
import type { Archivo, Destino, Pendiente } from '../api'
import { enviarFallos } from './fallos'
import logo from '../assets/logo.png'
import './movil.css'

type Vista = 'inicio' | 'subir' | 'organizados' | 'bandeja'
const LATIDO = 30_000
// Versión de esta app (la pone `pnpm movil:publicar`); 0 en versiones de prueba.
const VERSION = Number(import.meta.env.VITE_VERSION_MOVIL ?? 0)
interface Actualizacion { codigo: number; ruta: string }
const SINCRONIZAR = 60_000
// Los plugins nativos rechazan con objetos o textos, no siempre con Error.
const mensaje = (e: unknown): string => {
  if (e instanceof Error) return e.message
  if (typeof e === 'string') return e
  if (e && typeof e === 'object' && 'message' in e) return String((e as { message: unknown }).message)
  try { return JSON.stringify(e) } catch { return String(e) }
}
const plataforma = /Android/i.test(navigator.userAgent) ? 'android' : /iPhone|iPad/i.test(navigator.userAgent) ? 'ios' : null
const fecha = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString('es', { dateStyle: 'short', timeStyle: 'short' }) : 'nunca')
const etiquetasCola: Record<ElementoCola['estado'], string> = {
  espera: 'En espera', enviando: 'Enviando', recibido: 'Recibido', organizado: 'Organizado', bandeja: 'En la Bandeja', error: 'Error',
}

async function notificar(titulo: string, cuerpo: string) {
  try {
    let permiso = await isPermissionGranted()
    if (!permiso) permiso = (await requestPermission()) === 'granted'
    if (permiso) sendNotification({ title: titulo, body: cuerpo })
  } catch { /* sin notificaciones en este dispositivo */ }
}

/** Pide al abrir la app los permisos que usa: cámara (QR y fotos) y notificaciones (dudas de la IA). */
async function pedirPermisos(): Promise<{ camara: boolean; avisos: boolean }> {
  let camara = true
  let avisos = true
  if (plataforma) {
    try {
      const { checkPermissions, requestPermissions } = await import('@tauri-apps/plugin-barcode-scanner')
      camara = (await checkPermissions()) === 'granted' || (await requestPermissions()) === 'granted'
    } catch { camara = false }
  }
  try { avisos = (await isPermissionGranted()) || (await requestPermission()) === 'granted' } catch { avisos = false }
  return { camara, avisos }
}

async function escanearQR(): Promise<string | null> {
  if (!plataforma) return null // en el PC (modo de prueba) se pega el texto del QR
  const { scan, Format, checkPermissions, requestPermissions } = await import('@tauri-apps/plugin-barcode-scanner')
  let permiso = await checkPermissions()
  if (permiso !== 'granted') permiso = await requestPermissions()
  if (permiso !== 'granted') throw new Error('Nexora necesita permiso para usar la cámara. Actívalo en Ajustes → Aplicaciones → Nexora → Permisos, o pega el código abajo.')
  const r = await scan({ windowed: false, formats: [Format.QRCode] })
  return r.content
}

function Vincular({ alVincular, anterior }: { alVincular: (v: Vinculo) => void; anterior: Vinculo | null }) {
  const [texto, setTexto] = useState('')
  const [pegar, setPegar] = useState(!plataforma)
  const [nombre, setNombre] = useState(plataforma === 'ios' ? 'Mi iPhone' : 'Mi teléfono')
  const [qr, setQr] = useState<ContenidoQR | null>(null)
  const [error, setError] = useState('')
  const [ocupado, setOcupado] = useState(false)

  const usar = async (contenido: string) => {
    setError('')
    try {
      const leido = leerQR(contenido)
      if (anterior && anterior.pc_id !== leido.pc) setQr(leido) // pide confirmar antes de dejar el PC anterior
      else await canjear(leido)
    } catch (e) { setError(mensaje(e)) }
  }
  const canjear = async (leido: ContenidoQR) => {
    setOcupado(true)
    try {
      if (anterior) await olvidarTodo()
      const v = await vincular(leido, nombre.trim() || 'Mi teléfono', plataforma)
      await guardar('vinculo', v)
      alVincular(v)
    } catch (e) {
      setError(e instanceof SinConexion ? 'No encuentro el PC. Comprueba que el teléfono y el PC están en la misma Wi-Fi.' : mensaje(e))
    } finally { setOcupado(false); setQr(null) }
  }

  return (
    <section className="m-panel">
      <h2>Vincular con un PC</h2>
      <ol className="pasos"><li>Conecta el teléfono a la misma Wi-Fi que el PC.</li><li>En Nexora del PC: Configuración → Teléfono vinculado → Mostrar código QR.</li><li>Escanéalo aquí.</li></ol>
      <label className="m-campo">Nombre de este teléfono<input value={nombre} maxLength={80} onChange={(e) => setNombre(e.target.value)} /></label>
      {plataforma && <button className="m-boton principal" disabled={ocupado} onClick={async () => { const c = await escanearQR().catch((e) => { setError(mensaje(e)); setPegar(true); return null }); if (c) await usar(c) }}>{ocupado ? 'Vinculando…' : 'Escanear código QR'}</button>}
      {plataforma && !pegar && <button className="m-enlace" onClick={() => setPegar(true)}>¿No puedes escanear? Pegar el código</button>}
      {pegar && <>
        <textarea aria-label="Contenido del QR" rows={4} placeholder="Pega aquí el texto del código QR (en el PC: «Copiar código»)" value={texto} onChange={(e) => setTexto(e.target.value)} />
        <button className="m-boton principal" disabled={ocupado || !texto.trim()} onClick={() => void usar(texto.trim())}>{ocupado ? 'Vinculando…' : 'Vincular'}</button>
      </>}
      {qr && anterior && (
        <div className="m-aviso">
          <p>Este teléfono está vinculado a <strong>{anterior.pc_nombre}</strong>. ¿Desvincularlo y vincularlo a <strong>{qr.nombre}</strong>? Se borrarán la lista guardada y la cola pendiente.</p>
          <div className="m-fila"><button className="m-boton principal" onClick={() => void canjear(qr)}>Sí, cambiar de PC</button><button className="m-boton" onClick={() => setQr(null)}>Cancelar</button></div>
        </div>
      )}
      {error && <p className="m-error" role="alert">{error}</p>}
    </section>
  )
}

export default function AppMovil() {
  const [vinculo, setVinculo] = useState<Vinculo | null | undefined>(undefined)
  const [cambiandoPC, setCambiandoPC] = useState(false)
  const [vista, setVista] = useState<Vista>('inicio')
  const [enLinea, setEnLinea] = useState(false)
  const [maxMb, setMaxMb] = useState(200)
  const [ultimaSync, setUltimaSync] = useState<string | null>(null)
  const [cola, setCola] = useState<ElementoCola[]>([])
  const [organizados, setOrganizados] = useState<Organizado[]>([])
  const [pendientes, setPendientes] = useState<Pendiente[]>([])
  const [decisiones, setDecisiones] = useState<Decision[]>([])
  const [destinos, setDestinos] = useState<Destino[]>([])
  const [aviso, setAviso] = useState('')
  const [actualizacion, setActualizacion] = useState<Actualizacion | null>(null)
  const [permisosFaltan, setPermisosFaltan] = useState('')
  const vRef = useRef<Vinculo | null>(null)
  vRef.current = vinculo ?? null
  const ocupado = useRef(false)

  const recargarLocal = useCallback(async () => {
    setCola(await leer('cola', []))
    setOrganizados(await leer('organizados', []))
    setPendientes(await leer('pendientes', []))
    setDecisiones(await leer('decisiones', []))
    setDestinos(await leer('destinos', []))
    setUltimaSync(await leer('ultima_sync', null))
  }, [])

  const desvinculado = useCallback(async () => {
    await olvidarTodo() // el PC nos desvinculó: se borra token, caché y cola
    setVinculo(null)
    setEnLinea(false)
    setAviso('El PC desvinculó este teléfono. Se borraron la lista y la cola.')
    await recargarLocal()
  }, [recargarLocal])

  /** Descarga sola la versión nueva y abre el instalador de Android (instalar siempre requiere un toque del usuario). */
  const descargando = useRef(false)
  const instalar = async (a: Actualizacion) => {
    try {
      const r = await invoke<{ permiso: boolean }>('instalar_apk', { ruta: a.ruta })
      if (!r.permiso) setAviso('Permite «Instalar apps desconocidas» para Nexora y vuelve a tocar «Instalar actualización».')
    } catch (e) { setAviso(mensaje(e)) }
  }
  const prepararActualizacion = async (v: Vinculo, codigo: number) => {
    const lista = await leer<Actualizacion | null>('actualizacion', null)
    if (lista?.codigo === codigo || descargando.current) return
    descargando.current = true
    try {
      const ruta = await join(await appCacheDir(), `nexora-${codigo}.apk`)
      await descargarRuta(v, '/devices/app/apk', ruta, 'actualizacion')
      const nueva = { codigo, ruta }
      await guardar('actualizacion', nueva)
      setActualizacion(nueva)
      await instalar(nueva)
    } catch (e) { setAviso(`No se pudo descargar la actualización: ${mensaje(e)}`) } finally { descargando.current = false }
  }

  /** Latido + cola + decisiones + sincronización. Si el PC no está, todo queda en espera. */
  const ciclo = useCallback(async (completo: boolean) => {
    const v = vRef.current
    if (!v || ocupado.current) return
    ocupado.current = true
    try {
      const info = await pedir<{ max_subida_mb: number; ips: string[]; app_movil: { codigo: number } | null }>(v, '/devices/heartbeat', 'POST')
      if (info.app_movil && info.app_movil.codigo > VERSION && plataforma === 'android') void prepararActualizacion(v, info.app_movil.codigo)
      setEnLinea(true)
      setMaxMb(info.max_subida_mb)
      if (info.ips.length && info.ips.join() !== v.hosts.join()) {
        const nuevo = { ...v, hosts: info.ips }
        await guardar('vinculo', nuevo)
        setVinculo(nuevo)
      }
      await procesarDecisiones(v)
      const informes = await enviarFallos(v).catch(() => 0)
      if (informes) setAviso(`La app se cerró inesperadamente. Se envió ${informes === 1 ? 'el informe' : `${informes} informes`} del fallo a tu PC.`)
      await procesarCola(v, () => void leer<ElementoCola[]>('cola', []).then(setCola))
      if (completo) {
        const nuevas = await sincronizar(v)
        for (const p of nuevas.slice(0, 3)) await notificar('Nexora', `Nexora necesita tu ayuda con ${p.archivo.nombre}`)
      }
    } catch (e) {
      if (e instanceof Desvinculado) await desvinculado()
      else if (e instanceof SinConexion) setEnLinea(false)
      else setAviso(mensaje(e))
    } finally {
      ocupado.current = false
      await recargarLocal()
    }
  }, [desvinculado, recargarLocal])

  useEffect(() => {
    void (async () => {
      setVinculo(await leer<Vinculo | null>('vinculo', null))
      await recargarLocal()
      setActualizacion(await leer<Actualizacion | null>('actualizacion', null))
      const p = await pedirPermisos()
      const faltan = [!p.camara && 'cámara (para escanear el QR)', !p.avisos && 'notificaciones (para avisarte de dudas)'].filter(Boolean)
      setPermisosFaltan(faltan.length ? `Sin permiso de ${faltan.join(' y ')}. Puedes activarlo en Ajustes → Aplicaciones → Nexora → Permisos.` : '')
    })()
    avisarCambiosDeIp(async (ip) => {
      const v = vRef.current
      if (v && v.ip !== ip) { const nuevo = { ...v, ip }; await guardar('vinculo', nuevo); setVinculo(nuevo) }
    })
  }, [recargarLocal])

  useEffect(() => {
    if (!vinculo) return
    void ciclo(true)
    const latido = window.setInterval(() => void ciclo(false), LATIDO)
    const sync = window.setInterval(() => void ciclo(true), SINCRONIZAR)
    const volver = () => { if (document.visibilityState === 'visible') void ciclo(true) }
    document.addEventListener('visibilitychange', volver)
    return () => { window.clearInterval(latido); window.clearInterval(sync); document.removeEventListener('visibilitychange', volver) }
  }, [vinculo?.pc_id, ciclo]) // eslint-disable-line react-hooks/exhaustive-deps

  if (vinculo === undefined) return <main className="m-app" />
  if (!vinculo || cambiandoPC) {
    return (
      <main className="m-app">
        <header className="m-cabecera"><img src={logo} alt="" /><strong>Nexora</strong></header>
        {aviso && <p className="m-aviso">{aviso}</p>}
        <Vincular anterior={vinculo} alVincular={(v) => { setVinculo(v); setCambiandoPC(false); setAviso('') }} />
        {cambiandoPC && <button className="m-boton" onClick={() => setCambiandoPC(false)}>Cancelar</button>}
      </main>
    )
  }

  const enCola = cola.filter((c) => !['organizado', 'bandeja'].includes(c.estado)).length
  return (
    <main className="m-app">
      <header className="m-cabecera">
        <img src={logo} alt="" /><strong>Nexora</strong>
        <span className={`m-estado ${enLinea ? 'en-linea' : ''}`}><i />{enLinea ? 'PC en línea' : 'PC desconectado'}</span>
      </header>
      {aviso && <p className="m-aviso" onClick={() => setAviso('')}>{aviso}</p>}
      {permisosFaltan && <p className="m-aviso" onClick={() => setPermisosFaltan('')}>{permisosFaltan}</p>}
      {actualizacion && actualizacion.codigo > VERSION && (
        <div className="m-aviso m-actualizacion">
          <p>Hay una versión nueva de Nexora lista para instalar.</p>
          <button className="m-boton principal" onClick={() => void instalar(actualizacion)}>Instalar actualización</button>
        </div>
      )}

      {vista === 'inicio' && (
        <section className="m-panel">
          <div className="m-tarjeta">
            <span className={`punto ${enLinea ? 'en-linea' : ''}`} />
            <div><strong>{vinculo.pc_nombre}</strong><p>{enLinea ? 'En línea' : 'Desconectado'} · {vinculo.ip}:{vinculo.puerto}</p><p>Última sincronización: {fecha(ultimaSync)}</p></div>
          </div>
          <div className="m-resumen">
            <button onClick={() => setVista('subir')}><b>{enCola}</b>en cola</button>
            <button onClick={() => setVista('bandeja')}><b>{pendientes.length}</b>dudas</button>
            <button onClick={() => setVista('organizados')}><b>{organizados.length}</b>organizados</button>
          </div>
          <div className="m-fila">
            <button className="m-boton" onClick={() => void ciclo(true)}>Sincronizar ahora</button>
            <button className="m-boton" onClick={() => setCambiandoPC(true)}>Vincular otro PC</button>
          </div>
          <p className="m-texto m-version">Versión {VERSION || 'de prueba'}</p>
        </section>
      )}
      {vista === 'subir' && <Subir cola={cola} maxMb={maxMb} vinculo={vinculo} alCambiar={async () => { await recargarLocal(); void ciclo(false) }} avisar={setAviso} />}
      {vista === 'organizados' && <Organizados lista={organizados} enLinea={enLinea} vinculo={vinculo} avisar={setAviso} ultimaSync={ultimaSync} />}
      {vista === 'bandeja' && <Bandeja pendientes={pendientes} decisiones={decisiones} destinos={destinos} alCambiar={async () => { await recargarLocal(); void ciclo(true) }} />}

      <nav className="m-nav" aria-label="Secciones">
        {([['inicio', 'Inicio'], ['subir', 'Subir'], ['organizados', 'Organizados'], ['bandeja', `Bandeja${pendientes.length ? ` (${pendientes.length})` : ''}`]] as [Vista, string][]).map(([id, texto]) => (
          <button key={id} className={vista === id ? 'activa' : ''} aria-current={vista === id ? 'page' : undefined} onClick={() => setVista(id)}>{texto}</button>
        ))}
      </nav>
    </main>
  )
}

function Subir({ cola, maxMb, vinculo, alCambiar, avisar }: { cola: ElementoCola[]; maxMb: number; vinculo: Vinculo; alCambiar: () => void; avisar: (t: string) => void }) {
  const camara = useRef<HTMLInputElement>(null)
  const [copiando, setCopiando] = useState(false)
  const anadir = async (origenes: (string | File)[]) => {
    setCopiando(true)
    for (const o of origenes) {
      try { await encolar(o, maxMb * 1048576) } catch (e) { avisar(`${typeof o === 'string' ? o : o.name}: ${mensaje(e)}`) }
    }
    setCopiando(false)
    alCambiar()
  }
  const elegir = async () => {
    const r = await open({ multiple: true, title: 'Archivos para enviar al PC' }).catch(() => null)
    if (r) await anadir(Array.isArray(r) ? r : [r])
  }
  return (
    <section className="m-panel">
      <h2>Enviar al PC</h2>
      <p className="m-texto">Se envían a {vinculo.pc_nombre} y Nexora los organiza. Si el PC está desconectado, esperan aquí y se envían solos al volver. Máximo {maxMb} MB por archivo.</p>
      <div className="m-fila">
        <button className="m-boton principal" disabled={copiando} onClick={() => void elegir()}>Elegir archivos</button>
        <button className="m-boton" disabled={copiando} onClick={() => camara.current?.click()}>Cámara</button>
        <input ref={camara} type="file" accept="image/*" capture="environment" multiple hidden onChange={(e) => { const f = [...(e.target.files ?? [])]; e.target.value = ''; void anadir(f) }} />
      </div>
      <ul className="m-lista">
        {[...cola].reverse().map((c) => (
          <li key={c.id} data-cola={c.nombre}>
            <div><strong>{c.nombre}</strong>
              <p>{etiquetasCola[c.estado]}{c.estado === 'enviando' && ` (${Math.floor((c.enviados / c.tamano) * 100)}%)`}{c.error && ` · ${c.error}`}</p>
              {c.estado === 'enviando' && <progress max={c.tamano} value={c.enviados} />}
            </div>
            {['espera', 'enviando', 'error'].includes(c.estado) && <button className="m-boton" onClick={async () => { await quitarDeCola(vinculo, c.id); alCambiar() }}>Cancelar</button>}
            {['organizado', 'bandeja'].includes(c.estado) && <button className="m-boton" onClick={async () => { await quitarDeCola(null, c.id); alCambiar() }}>Quitar</button>}
          </li>
        ))}
        {!cola.length && <p className="m-texto">No hay envíos.</p>}
      </ul>
    </section>
  )
}

function Organizados({ lista, enLinea, vinculo, avisar, ultimaSync }: { lista: Organizado[]; enLinea: boolean; vinculo: Vinculo; avisar: (t: string) => void; ultimaSync: string | null }) {
  const [q, setQ] = useState('')
  const [carpeta, setCarpeta] = useState('')
  const [tipo, setTipo] = useState('')
  const [dias, setDias] = useState(0)
  const [semantica, setSemantica] = useState<string[] | null>(null)
  const [progreso, setProgreso] = useState<Record<string, number>>({})
  const [listos, setListos] = useState<Record<string, string>>({})

  useEffect(() => {
    const parar = listen<{ id: string; recibidos: number; total: number | null }>('descarga', (e) =>
      setProgreso((p) => ({ ...p, [e.payload.id]: e.payload.total ? e.payload.recibidos / e.payload.total : 0 })))
    return () => { void parar.then((f) => f()) }
  }, [])

  const carpetas = useMemo(() => [...new Set(lista.map((o) => o.carpeta_etiqueta ?? ''))].filter(Boolean).sort(), [lista])
  const tipos = useMemo(() => [...new Set(lista.map((o) => o.tipo))].sort(), [lista])
  const visibles = lista
    .filter((o) => !carpeta || o.carpeta_etiqueta === carpeta)
    .filter((o) => !tipo || o.tipo === tipo)
    .filter((o) => !dias || Date.now() - new Date(o.clasificado_en ?? o.actualizado_en).getTime() < dias * 86400000)
    .filter((o) => (semantica ? semantica.includes(o.id) : !q || `${o.nombre} ${o.resumen_ejecutivo ?? ''} ${o.carpeta_etiqueta ?? ''}`.toLowerCase().includes(q.toLowerCase())))
    .sort((a, b) => (b.clasificado_en ?? b.actualizado_en).localeCompare(a.clasificado_en ?? a.actualizado_en))

  const buscarPorSignificado = async () => {
    try { setSemantica((await pedir<{ resultados: { archivo: Archivo }[] }>(vinculo, `/files/search?limite=50&q=${encodeURIComponent(q)}`)).resultados.map((r) => r.archivo.id)) } catch (e) { avisar(mensaje(e)) }
  }
  const descargar = async (o: Organizado) => {
    setProgreso((p) => ({ ...p, [o.id]: 0 }))
    try { setListos((l) => ({ ...l, [o.id]: '' })); const ruta = await bajar(vinculo, o); setListos((l) => ({ ...l, [o.id]: ruta })) } catch (e) { avisar(mensaje(e)) } finally { setProgreso(({ [o.id]: _, ...resto }) => resto) }
  }

  return (
    <section className="m-panel">
      <h2>Organizados <span>{visibles.length}</span></h2>
      <p className="m-texto">Última sincronización: {fecha(ultimaSync)}{!enLinea && ' · El PC está desconectado: ves la última copia guardada.'}</p>
      <div className="m-fila">
        <input aria-label="Buscar" placeholder="Buscar…" value={q} onChange={(e) => { setQ(e.target.value); setSemantica(null) }} />
        <button className="m-boton" disabled={!enLinea || !q.trim()} title={enLinea ? '' : 'El PC está desconectado'} onClick={() => void buscarPorSignificado()}>Por significado</button>
      </div>
      <div className="m-filtros">
        <select aria-label="Carpeta" value={carpeta} onChange={(e) => setCarpeta(e.target.value)}><option value="">Todas las carpetas</option>{carpetas.map((c) => <option key={c}>{c}</option>)}</select>
        <select aria-label="Tipo" value={tipo} onChange={(e) => setTipo(e.target.value)}><option value="">Todos los tipos</option>{tipos.map((t) => <option key={t}>{t}</option>)}</select>
        <select aria-label="Fecha" value={dias} onChange={(e) => setDias(Number(e.target.value))}><option value={0}>Cualquier fecha</option><option value={1}>Hoy</option><option value={7}>Esta semana</option><option value={30}>Este mes</option></select>
      </div>
      <ul className="m-lista">
        {visibles.map((o) => (
          <li key={o.id} data-organizado={o.nombre}>
            <div>
              <strong>{o.nombre}</strong>
              <p>{o.tipo.toUpperCase()} → {o.carpeta_etiqueta} · {fecha(o.clasificado_en ?? o.actualizado_en)}{o.confianza != null && ` · ${Math.round(o.confianza * 100)}%`}</p>
              {o.resumen_ejecutivo && <p className="m-resumen-corto">{o.resumen_ejecutivo}</p>}
              {o.id in progreso && <progress max={1} value={progreso[o.id]} />}
            </div>
            {listos[o.id]
              ? <button className="m-boton" onClick={() => void openPath(listos[o.id]).catch((e) => avisar(mensaje(e)))}>Abrir</button>
              : <button className="m-boton" disabled={!enLinea || o.id in progreso} title={enLinea ? '' : 'El PC está desconectado'} onClick={() => void descargar(o)}>{enLinea ? 'Descargar' : 'El PC está desconectado'}</button>}
          </li>
        ))}
        {!visibles.length && <p className="m-texto">No hay archivos que coincidan.</p>}
      </ul>
    </section>
  )
}

function Bandeja({ pendientes, decisiones, destinos, alCambiar }: { pendientes: Pendiente[]; decisiones: Decision[]; destinos: Destino[]; alCambiar: () => void }) {
  const [eligiendo, setEligiendo] = useState('')
  const [filtro, setFiltro] = useState('')
  const decididas = new Set(decisiones.map((d) => d.pendiente_id))
  const resolver = async (p: Pendiente, tipo: Decision['tipo'], carpeta?: string) => {
    await decidir({ tipo, archivo_id: p.archivo.id, pendiente_id: p.pendiente_id, carpeta, nombre: p.archivo.nombre })
    setEligiendo('')
    alCambiar()
  }
  const visibles = pendientes.filter((p) => !decididas.has(p.pendiente_id))
  return (
    <section className="m-panel">
      <h2>Bandeja de Dudas <span>{visibles.length}</span></h2>
      {decisiones.length > 0 && <p className="m-texto">{decisiones.length} decisión(es) esperando a que el PC esté en línea.</p>}
      <ul className="m-lista">
        {visibles.map((p) => (
          <li key={p.pendiente_id} className="columna" data-duda={p.archivo.nombre}>
            <strong>{p.archivo.nombre}</strong>
            {p.archivo.resumen_ejecutivo && <p className="m-resumen-corto">{p.archivo.resumen_ejecutivo}</p>}
            <p>{p.duplicado_de ? `Copia exacta de ${p.duplicado_de.nombre}` : `Sugerida: ${p.carpeta_etiqueta}${p.carpeta_existe ? '' : ' (nueva)'} · ${Math.round(p.confianza * 100)}%`}</p>
            <div className="m-fila">
              <button className="m-boton principal" onClick={() => void resolver(p, 'decidir', p.carpeta_sugerida)}>Aprobar</button>
              <button className="m-boton" onClick={() => setEligiendo(eligiendo === p.pendiente_id ? '' : p.pendiente_id)}>Elegir otra</button>
              <button className="m-boton" onClick={() => void resolver(p, 'ignorar')}>Ignorar</button>
            </div>
            {eligiendo === p.pendiente_id && <>
              <input aria-label="Buscar carpeta" placeholder="Busca una carpeta…" value={filtro} onChange={(e) => setFiltro(e.target.value)} />
              <div className="m-opciones">
                {destinos.filter((d) => d.etiqueta.toLowerCase().includes(filtro.toLowerCase())).slice(0, 12).map((d) => <button key={d.valor} onClick={() => void resolver(p, 'decidir', d.valor)}>{d.etiqueta}</button>)}
              </div>
            </>}
          </li>
        ))}
        {!visibles.length && <p className="m-texto">Todo en orden: no hay dudas.</p>}
      </ul>
    </section>
  )
}
