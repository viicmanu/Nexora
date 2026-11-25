import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import {
  alSoltarArchivos, api, elegirArchivos, elegirCarpeta, encenderMotor, MotorApagado,
  type Ajustes, type Archivo, type Carpeta, type Destino, type Evento, type Pendiente, type Propuesta, type PruebaIA, type Resumen,
} from './api'
import { Indicaciones, NuevaIndicacion } from './Indicaciones'

type Theme = 'light' | 'dark' | 'system'
type Vista = 'dashboard' | 'inbox' | 'folder' | 'rules' | 'history' | 'settings'

const icons: Record<string, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
  inbox: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></>,
  folder: <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  upload: <><path d="M12 16V3M7 8l5-5 5 5"/><path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/></>,
  chevron: <path d="m7 10 5 5 5-5"/>,
  rules: <><path d="M4 5h16v11H9l-5 4Z"/><path d="M8 9h8M8 12h5"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
}

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{icons[name]}</svg>
}

const themeOptions: { value: Theme; label: string; icon: string; modo: 'claro' | 'oscuro' | 'auto' }[] = [
  { value: 'light', label: 'Claro', icon: '☀', modo: 'claro' },
  { value: 'dark', label: 'Oscuro', icon: '☾', modo: 'oscuro' },
  { value: 'system', label: 'Automático (Sistema)', icon: '◉', modo: 'auto' },
]

const nav: [Vista, string][] = [
  ['dashboard', 'Dashboard'],
  ['inbox', 'Bandeja de Dudas'],
  ['folder', 'Carpetas Vigiladas'],
  ['rules', 'Indicaciones'],
  ['history', 'Historial de Aprendizaje'],
  ['settings', 'Configuración'],
]

const subtitulos: Record<Vista, string> = {
  dashboard: 'Tu espacio digital está bajo control.',
  inbox: 'La IA necesita tu criterio para estos archivos.',
  folder: 'Nexora organiza los archivos nuevos que lleguen a estas carpetas.',
  rules: 'Dile a la IA cómo quieres organizar tus archivos.',
  history: 'Busca tus archivos y pide a la IA que te los explique.',
  settings: 'Ajusta Nexora a tu manera.',
}

const etiquetas: Record<Archivo['estado'], string> = {
  procesando: 'Analizando', pendiente: 'Pendiente', clasificado: 'Clasificado', corregido: 'Corregido por ti', error: 'Error',
}

/** Nombre corto de una carpeta: "Documentos/Universidad" en lugar de la ruta completa. */
function nombrar(valor: string, destinos: Destino[]) {
  return destinos.find((d) => d.valor.toLowerCase() === valor.toLowerCase())?.etiqueta ?? valor
}

const mensaje = (e: unknown) => (e instanceof Error ? e.message : String(e))

function haceCuanto(fecha: string) {
  const s = Math.max(0, (Date.now() - new Date(fecha).getTime()) / 1000)
  if (s < 60) return 'ahora'
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`
  return new Date(fecha).toLocaleDateString('es')
}

function tipoDe(nombre: string) {
  return (nombre.includes('.') ? nombre.split('.').pop()! : 'FILE').slice(0, 4).toUpperCase()
}

type Avisar = (texto: string, error?: boolean) => void
type Resolver = (d: Pendiente, carpeta: string) => Promise<void>

function SelectorCarpeta({ nombre, primero = [], excluir, destinos, ocupado, elegir }: {
  nombre: string; primero?: string[]; excluir?: string | null; destinos: Destino[]; ocupado: boolean; elegir: (carpeta: string) => void
}) {
  const [filtro, setFiltro] = useState('')
  const texto = filtro.trim().toLowerCase()
  const candidatas = [...new Set([...primero, ...destinos.map((d) => d.valor)])]
    .filter((v) => v !== excluir)
    .map((valor) => ({ valor, etiqueta: nombrar(valor, destinos), existe: destinos.some((d) => d.valor === valor) || /^[a-z]:/i.test(valor) }))
    .filter((c) => c.existe && c.etiqueta.toLowerCase().includes(texto))
  const examinar = async () => {
    const carpeta = await elegirCarpeta(`¿Dónde pongo ${nombre}?`).catch(() => null)
    if (carpeta) elegir(carpeta)
  }
  return <>
    <div className="fila">
      <input autoFocus aria-label="Buscar carpeta" placeholder="Busca entre tus carpetas…" value={filtro} onChange={(e) => setFiltro(e.target.value)} />
      <button type="button" className="boton" disabled={ocupado} onClick={examinar}>Otra carpeta…</button>
    </div>
    <div className="opciones">
      {candidatas.slice(0, 12).map((c) => <button type="button" key={c.valor} title={c.valor} disabled={ocupado} onClick={() => elegir(c.valor)}><Icon name="folder" size={14} /> {c.etiqueta}</button>)}
      {candidatas.length > 12 && <p>y {candidatas.length - 12} más: escribe para filtrar.</p>}
      {!candidatas.length && <p>Ninguna coincide. Usa «Otra carpeta…».</p>}
    </div>
  </>
}

/** El usuario cuenta qué quedó mal; la IA propone el cambio y solo se mueve si lo confirma. */
function Corregir({ archivo, destinos, avisar, alTerminar }: { archivo?: Archivo; destinos: Destino[]; avisar: Avisar; alTerminar: () => Promise<void> }) {
  const [texto, setTexto] = useState('')
  const [propuesta, setPropuesta] = useState<Propuesta | null>(null)
  const [elegido, setElegido] = useState<Archivo | null>(null)
  const [eligiendo, setEligiendo] = useState(false)
  const [ocupado, setOcupado] = useState<'' | 'pensando' | 'moviendo'>('')

  const cancelar = () => { setPropuesta(null); setElegido(null); setEligiendo(false) }
  const enviar = async (e: FormEvent) => {
    e.preventDefault()
    setOcupado('pensando')
    cancelar()
    try {
      const p = await api.proponerCorreccion(texto.trim(), archivo?.id ?? null)
      setPropuesta(p)
      setElegido(p.archivo)
      setEligiendo(!p.carpeta)
    } catch (err) {
      avisar(mensaje(err), true)
      if (archivo) { // aunque la IA falle, se puede elegir la carpeta a mano
        setPropuesta({ archivo, candidatos: [archivo], carpeta: null, carpeta_etiqueta: null, carpeta_existe: false, modelo: '' })
        setElegido(archivo)
        setEligiendo(true)
      }
    } finally { setOcupado('') }
  }
  const mover = async (carpeta: string) => {
    if (!elegido) return
    setOcupado('moviendo')
    try {
      const r = await api.decidir(elegido.id, carpeta, texto.trim())
      avisar(r.aprendizaje_registrado ? `${elegido.nombre} → ${nombrar(r.carpeta_elegida, destinos)}. La IA lo recordará.` : `${elegido.nombre} se movió, pero no se pudo guardar el aprendizaje.`, !r.aprendizaje_registrado)
      setTexto('')
      cancelar()
      await alTerminar()
    } catch (err) { avisar(mensaje(err), true) } finally { setOcupado('') }
  }

  return (
    <div className="corregir">
      <form onSubmit={enviar}>
        <textarea
          aria-label="Mensaje para la IA" rows={2} maxLength={500} disabled={!!ocupado} value={texto} onChange={(e) => { setTexto(e.target.value); cancelar() }}
          placeholder={archivo ? 'Ejemplo: va en Universidad, es del semestre pasado' : 'Ejemplo: la factura de agua quedó mal, debería estar en Hogar'}
        />
        <button className="boton principal" disabled={!!ocupado || texto.trim().length < 3}>{ocupado === 'pensando' ? 'Pensando…' : 'Decirle a la IA'}</button>
      </form>
      {propuesta && (
        <div className="respuesta propuesta">
          {!propuesta.candidatos.length && <p>No encontré ningún archivo que coincida. Escribe su nombre o usa «¿Mal ubicado?» en la lista.</p>}
          {propuesta.candidatos.length > 0 && !elegido && <>
            <p>¿De qué archivo hablas?</p>
            <div className="opciones">{propuesta.candidatos.map((c) => <button key={c.id} title={c.ruta_actual} onClick={() => setElegido(c)}>{c.nombre}</button>)}</div>
          </>}
          {elegido && <>
            {propuesta.carpeta && !eligiendo
              ? <p>¿Muevo <strong>{elegido.nombre}</strong> a <strong>{propuesta.carpeta_etiqueta}</strong>{!propuesta.carpeta_existe && ' (carpeta nueva)'}?</p>
              : <p>¿A qué carpeta muevo <strong>{elegido.nombre}</strong>?</p>}
            <p className="ruta">Ahora está en {elegido.ruta_actual}</p>
            <div className="fila">
              {propuesta.carpeta && !eligiendo && <>
                <button className="boton principal" disabled={!!ocupado} onClick={() => mover(propuesta.carpeta!)}>{ocupado === 'moviendo' ? 'Moviendo…' : propuesta.carpeta_existe ? 'Sí, mover' : 'Crear y mover'}</button>
                <button className="boton" disabled={!!ocupado} onClick={() => setEligiendo(true)}>Elegir otra</button>
              </>}
              <button className="boton" disabled={!!ocupado} onClick={cancelar}>Cancelar</button>
            </div>
            {eligiendo && <SelectorCarpeta nombre={elegido.nombre} excluir={elegido.estado === 'pendiente' ? null : elegido.carpeta} destinos={destinos} ocupado={!!ocupado} elegir={(c) => void mover(c)} />}
          </>}
        </div>
      )}
    </div>
  )
}

function TarjetaDuda({ duda, destinos, resolver }: { duda: Pendiente; destinos: Destino[]; resolver: Resolver }) {
  const [modo, setModo] = useState<'' | 'cambiar' | 'nueva'>('')
  const [nueva, setNueva] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const elegir = async (carpeta: string) => {
    setOcupado(true)
    try { await resolver(duda, carpeta) } finally { setOcupado(false) }
  }
  const alternar = (m: 'cambiar' | 'nueva') => setModo(modo === m ? '' : m)

  return (
    <div className="file-card" data-archivo={duda.archivo.nombre}>
      <div className={`file-icon ${duda.archivo.nombre.endsWith('.docx') ? 'docx' : ''}`}><span>{tipoDe(duda.archivo.nombre)}</span></div>
      <div className="file-info">
        <div className="file-title"><div><strong>{duda.archivo.nombre}</strong><p>{duda.archivo.resumen_ejecutivo}</p></div><span className="confidence">{Math.round(duda.confianza * 100)}% segura</span></div>
        {!duda.carpeta_existe && <p className="pregunta">No encontré una carpeta tuya para este archivo. ¿Dónde lo pongo?</p>}
        <div className="suggestion"><small>{duda.carpeta_existe ? 'TU CARPETA' : 'CARPETA NUEVA'}</small><strong title={duda.carpeta_sugerida}><Icon name="folder" size={15} /> {duda.carpeta_etiqueta}</strong></div>
        {duda.razonamiento?.startsWith('Según tu indicación') && <p className="panel-description">{duda.razonamiento}</p>}
        {!duda.archivo.existe_en_disco && <p className="aviso-error">El archivo ya no está en su ubicación.</p>}
        <div className="file-actions">
          <button className="confirm" disabled={ocupado} onClick={() => elegir(duda.carpeta_sugerida)}>{ocupado ? 'Moviendo…' : duda.carpeta_existe ? 'Confirmar' : 'Crear y mover'}</button>
          <button className="change" disabled={ocupado} aria-expanded={modo === 'cambiar'} onClick={() => alternar('cambiar')}>{duda.carpeta_existe ? 'Cambiar carpeta' : 'Elegir carpeta'} <Icon name="chevron" size={15} /></button>
          <button className="create" disabled={ocupado} aria-expanded={modo === 'nueva'} onClick={() => alternar('nueva')}><Icon name="plus" size={15} /> Nueva</button>
        </div>
        {modo === 'cambiar' && <SelectorCarpeta nombre={duda.archivo.nombre} primero={duda.alternativas} excluir={duda.carpeta_sugerida} destinos={destinos} ocupado={ocupado} elegir={(c) => void elegir(c)} />}
        {modo === 'nueva' && (
          <form className="fila" onSubmit={(e) => { e.preventDefault(); if (nueva.trim()) void elegir(nueva) }}>
            <input autoFocus aria-label="Nueva carpeta" placeholder="Ejemplo: Finanzas/Hogar" value={nueva} onChange={(e) => setNueva(e.target.value)} />
            <button className="boton principal" disabled={ocupado || !nueva.trim()}>Crear carpeta</button>
          </form>
        )}
      </div>
    </div>
  )
}

function ListaDudas({ dudas, destinos, resolver }: { dudas: Pendiente[]; destinos: Destino[]; resolver: Resolver }) {
  if (!dudas.length) return <div className="empty-state"><span>✓</span><strong>Todo está en orden</strong><p>No hay archivos pendientes de revisión.</p></div>
  return <>{dudas.map((d) => <TarjetaDuda key={d.pendiente_id} duda={d} destinos={destinos} resolver={resolver} />)}</>
}

function Carpetas({ avisar }: { avisar: Avisar }) {
  const [lista, setLista] = useState<Carpeta[]>([])
  const [ruta, setRuta] = useState('')
  const [recursiva, setRecursiva] = useState(false)
  const cargar = useCallback(() => api.carpetas().then(setLista).catch((e) => avisar(mensaje(e), true)), [avisar])
  useEffect(() => { void cargar() }, [cargar])

  const agregar = async (e: FormEvent) => {
    e.preventDefault()
    try {
      await api.agregarCarpeta(ruta, recursiva)
      setRuta('')
      avisar('Carpeta añadida. Nexora ya la está vigilando.')
      await cargar()
    } catch (err) { avisar(mensaje(err), true) }
  }
  const quitar = async (c: Carpeta) => {
    try {
      await api.quitarCarpeta(c.id)
      avisar('Nexora dejó de vigilar la carpeta. Sus archivos no se tocaron.')
      await cargar()
    } catch (err) { avisar(mensaje(err), true) }
  }
  const examinar = async () => {
    try {
      const elegida = await elegirCarpeta('Elige la carpeta que Nexora debe vigilar')
      if (elegida) setRuta(elegida)
    } catch (err) { avisar(mensaje(err), true) }
  }

  return (
    <article className="panel pagina">
      <h2>Carpetas Vigiladas <span>{lista.length}</span></h2>
      <p className="panel-description">Los archivos nuevos que lleguen aquí se analizan con la IA y pueden moverse automáticamente.</p>
      <form className="fila" onSubmit={agregar}>
        <input aria-label="Ruta de la carpeta" placeholder="C:\Users\...\Downloads" value={ruta} onChange={(e) => setRuta(e.target.value)} />
        <button type="button" className="boton" onClick={examinar}>Examinar…</button>
        <button className="boton principal" disabled={!ruta.trim()}>Añadir</button>
      </form>
      <label className="casilla"><input type="checkbox" checked={recursiva} onChange={(e) => setRecursiva(e.target.checked)} /> Incluir subcarpetas</label>
      <div className="lista">
        {lista.map((c) => (
          <div className="item" key={c.id}>
            <Icon name="folder" />
            <div><strong>{c.ruta}</strong><p>{c.existe ? (c.recursiva ? 'Vigilando con subcarpetas' : 'Vigilando') : 'La carpeta ya no existe'}</p></div>
            <button className="boton" onClick={() => quitar(c)}>Quitar</button>
          </div>
        ))}
        {!lista.length && <p className="panel-description">No hay carpetas vigiladas. Añade una para empezar.</p>}
      </div>
    </article>
  )
}

function Historial({ eventos, destinos, avisar, refrescar }: { eventos: Evento[]; destinos: Destino[]; avisar: Avisar; refrescar: () => Promise<void> }) {
  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState<Archivo[] | null>(null)
  const [abierto, setAbierto] = useState('')
  const [corrigiendo, setCorrigiendo] = useState('')
  const [pregunta, setPregunta] = useState('')
  const [respuesta, setRespuesta] = useState('')
  const [cargando, setCargando] = useState(false)

  const buscar = async (e: FormEvent) => {
    e.preventDefault()
    if (!q.trim()) return setResultados(null)
    try { setResultados((await api.buscar(q)).resultados.map((r) => r.archivo)) } catch (err) { avisar(mensaje(err), true) }
  }
  const explicar = async (id: string) => {
    setCargando(true)
    setRespuesta('')
    try {
      const r = await api.explicar(id, pregunta.trim() || null)
      setRespuesta(r.respuesta + (r.texto_truncado ? '\n\n(El documento es largo: la IA solo leyó el comienzo.)' : ''))
    } catch (err) { avisar(mensaje(err), true) } finally { setCargando(false) }
  }
  const reintentar = async (id: string) => {
    try { await api.reintentar(id); avisar('Archivo en cola. La IA lo intentará de nuevo.'); await refrescar() } catch (err) { avisar(mensaje(err), true) }
  }
  const trasCorregir = async () => { setCorrigiendo(''); setResultados(null); setQ(''); await refrescar() }
  const alternar = (id: string) => { setAbierto(abierto === id ? '' : id); setRespuesta(''); setPregunta('') }
  const lista: (Archivo & { error?: string | null })[] = resultados ?? eventos

  return (
    <div className="historial">
    <article className="panel pagina">
      <h2>¿Un archivo quedó mal?</h2>
      <p className="panel-description">Cuéntaselo a la IA con tus palabras. Te propondrá el cambio y aprenderá de él; no mueve nada sin tu permiso.</p>
      <Corregir destinos={destinos} avisar={avisar} alTerminar={trasCorregir} />
    </article>
    <article className="panel pagina">
      <h2>{resultados ? 'Resultados' : 'Historial'} <span>{lista.length}</span></h2>
      <form className="fila" onSubmit={buscar}>
        <input aria-label="Buscar archivos" placeholder="Busca por nombre o contenido: factura de energía…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="boton principal">Buscar</button>
        {resultados && <button type="button" className="boton" onClick={() => { setResultados(null); setQ('') }}>Limpiar</button>}
      </form>
      <div className="lista">
        {lista.map((a) => (
          <div className="item columna" key={a.id}>
            <div className="item-fila">
              <div className="file-icon"><span>{tipoDe(a.nombre)}</span></div>
              <div>
                <strong>{a.nombre}</strong>
                <p>{a.estado === 'clasificado' && !a.carpeta ? 'Se dejó en su sitio' : etiquetas[a.estado]}{a.carpeta && (a.estado === 'clasificado' || a.estado === 'corregido') && ` · ${nombrar(a.carpeta, destinos)}`} · {haceCuanto(a.actualizado_en)}</p>
                <p className="ruta">{a.ruta_actual}{!a.existe_en_disco && ' (ya no está ahí)'}</p>
                {a.error && <p className="aviso-error">{a.error}</p>}
              </div>
              {a.estado === 'error' && <button className="boton" onClick={() => reintentar(a.id)}>Reintentar</button>}
              {(a.estado === 'clasificado' || a.estado === 'corregido') && a.existe_en_disco && <button className="boton" aria-expanded={corrigiendo === a.id} onClick={() => setCorrigiendo(corrigiendo === a.id ? '' : a.id)}>¿Mal ubicado?</button>}
              <button className="boton" aria-expanded={abierto === a.id} disabled={!a.existe_en_disco || cargando} onClick={() => alternar(a.id)}>Preguntar a la IA</button>
            </div>
            {corrigiendo === a.id && <Corregir archivo={a} destinos={destinos} avisar={avisar} alTerminar={trasCorregir} />}
            {abierto === a.id && (
              <div className="explicacion">
                {a.resumen_ejecutivo && <p>{a.resumen_ejecutivo}</p>}
                <form className="fila" onSubmit={(e) => { e.preventDefault(); void explicar(a.id) }}>
                  <input aria-label="Pregunta" placeholder="Escribe una pregunta, o déjalo vacío para una explicación" value={pregunta} onChange={(e) => setPregunta(e.target.value)} />
                  <button className="boton principal" disabled={cargando}>{cargando ? 'Pensando…' : 'Explicar'}</button>
                </form>
                {respuesta && <div className="respuesta">{respuesta}</div>}
              </div>
            )}
          </div>
        ))}
        {!lista.length && <p className="panel-description">{resultados ? 'No se encontró ningún archivo.' : 'Aún no hay archivos procesados.'}</p>}
      </div>
    </article>
    </div>
  )
}

function Configuracion({ nombre, setNombre, theme, cambiarTema, avisar }: { nombre: string; setNombre: (n: string) => void; theme: Theme; cambiarTema: (t: Theme) => void; avisar: Avisar }) {
  const [ajustes, setAjustes] = useState<Ajustes | null>(null)
  const [umbral, setUmbral] = useState(85)
  const [borrador, setBorrador] = useState(nombre)
  const [prueba, setPrueba] = useState<PruebaIA | null>(null)
  const [fallo, setFallo] = useState('')
  const [probando, setProbando] = useState(false)
  useEffect(() => {
    api.ajustes().then((a) => { setAjustes(a); setUmbral(Math.round(a.umbral_confianza * 100)) }).catch((e) => avisar(mensaje(e), true))
  }, [avisar])

  const guardar = async () => {
    try { setAjustes(await api.guardarUmbral(umbral / 100)); avisar('Nivel de confianza guardado.') } catch (e) { avisar(mensaje(e), true) }
  }
  const probar = async () => {
    setProbando(true); setPrueba(null); setFallo('')
    try { setPrueba(await api.probarIA()) } catch (e) { setFallo(mensaje(e)) } finally { setProbando(false) }
  }

  return (
    <div className="ajustes">
      <article className="panel pagina">
        <h2>Inteligencia Artificial</h2>
        <p className="panel-description">Motor principal: <strong>{ajustes?.llm ?? '…'}</strong></p>
        <p className="panel-description">Si se agota la cuota: <strong>{ajustes ? ajustes.respaldo ?? 'sin respaldo' : '…'}</strong></p>
        <button className="boton principal" disabled={probando} onClick={probar}>{probando ? 'Probando…' : 'Probar la IA'}</button>
        {prueba && <div className="respuesta"><strong>✓ La IA funciona</strong> con {prueba.modelo} ({prueba.segundos} s){'\n'}Recibo de prueba → {prueba.carpeta} ({Math.round(prueba.confianza * 100)}%){'\n'}{prueba.resumen}</div>}
        {fallo && <p className="aviso-error">La IA no respondió: {fallo}</p>}
      </article>
      <article className="panel pagina">
        <h2>Clasificación automática</h2>
        <p className="panel-description">Nexora mueve el archivo sola cuando está al menos así de segura:</p>
        <label className="casilla"><input type="range" aria-label="Nivel de confianza" min={50} max={100} value={umbral} onChange={(e) => setUmbral(Number(e.target.value))} /> {umbral}%</label>
        <button className="boton principal" onClick={guardar}>Guardar nivel</button>
        <p className="panel-description">Las carpetas nuevas se crean en <strong>{ajustes?.raiz_organizada ?? '…'}</strong> si no tienes otra carpeta.</p>
        <p className="panel-description">Busca tus carpetas en: {ajustes?.zonas_busqueda.join(', ') ?? '…'}</p>
      </article>
      <article className="panel pagina">
        <h2>Perfil</h2>
        <form className="fila" onSubmit={(e) => { e.preventDefault(); setNombre(borrador.trim()); avisar('Nombre guardado.') }}>
          <input aria-label="Tu nombre" placeholder="Tu nombre" maxLength={30} value={borrador} onChange={(e) => setBorrador(e.target.value)} />
          <button className="boton principal">Guardar nombre</button>
        </form>
      </article>
      <article className="panel pagina">
        <h2>Apariencia</h2>
        <div className="opciones">
          {themeOptions.map((o) => <button key={o.value} className={theme === o.value ? 'elegida' : ''} aria-pressed={theme === o.value} onClick={() => cambiarTema(o.value)}>{o.icon} {o.label}</button>)}
        </div>
      </article>
    </div>
  )
}

function App() {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem('nexora-theme') as Theme) || 'system')
  const [nombre, setNombreEstado] = useState(() => localStorage.getItem('nexora-nombre') ?? '')
  const [vista, setVista] = useState<Vista>('dashboard')
  const [themeOpen, setThemeOpen] = useState(false)
  const [masOpen, setMasOpen] = useState(false)
  const [aviso, setAviso] = useState<{ texto: string; error: boolean } | null>(null)
  const [dragging, setDragging] = useState(false)
  const [apagado, setApagado] = useState('Conectando con el motor…')
  const [encendiendo, setEncendiendo] = useState(false)
  const [resumen, setResumen] = useState<Resumen | null>(null)
  const [dudas, setDudas] = useState<Pendiente[]>([])
  const [eventos, setEventos] = useState<Evento[]>([])
  const [destinos, setDestinos] = useState<Destino[]>([])
  const themeRef = useRef<HTMLDivElement>(null)
  const masRef = useRef<HTMLDivElement>(null)
  const temporizador = useRef(0)

  const avisar = useCallback<Avisar>((texto, error = false) => {
    window.clearTimeout(temporizador.current)
    setAviso({ texto, error })
    temporizador.current = window.setTimeout(() => setAviso(null), error ? 6000 : 3200)
  }, [])

  const refrescar = useCallback(async () => {
    try {
      const [r, d, e, c] = await Promise.all([api.resumen(), api.pendientes(), api.actividad(), api.destinos()])
      setResumen(r); setDudas(d); setEventos(e); setDestinos(c); setApagado('')
    } catch (err) {
      setApagado(err instanceof MotorApagado ? err.message : `El motor respondió con un error: ${mensaje(err)}`)
    }
  }, [])

  const encender = useCallback(async () => {
    setEncendiendo(true)
    try {
      await encenderMotor()
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 1000))
        try { await api.resumen(); break } catch { /* todavía arrancando */ }
      }
      await refrescar()
    } catch (err) {
      setApagado(mensaje(err))
    } finally {
      setEncendiendo(false)
    }
  }, [refrescar])

  useEffect(() => {
    // Al abrir la app, si el motor no responde se enciende solo.
    void api.resumen().then(refrescar, (err) => (err instanceof MotorApagado ? encender() : refrescar()))
    const id = window.setInterval(() => void refrescar(), 4000)
    return () => window.clearInterval(id)
  }, [refrescar, encender])

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => { document.documentElement.dataset.theme = theme === 'system' ? (media.matches ? 'dark' : 'light') : theme }
    apply()
    localStorage.setItem('nexora-theme', theme)
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [theme])

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!themeRef.current?.contains(event.target as Node)) setThemeOpen(false)
      if (!masRef.current?.contains(event.target as Node)) setMasOpen(false)
    }
    // Soltar un archivo fuera de la zona no debe sacar a la app de su página.
    const evitar = (event: DragEvent) => event.preventDefault()
    document.addEventListener('mousedown', close)
    window.addEventListener('dragover', evitar)
    window.addEventListener('drop', evitar)
    return () => {
      document.removeEventListener('mousedown', close)
      window.removeEventListener('dragover', evitar)
      window.removeEventListener('drop', evitar)
    }
  }, [])

  const cambiarTema = (t: Theme) => {
    setTheme(t)
    setThemeOpen(false)
    api.guardarTema(themeOptions.find((o) => o.value === t)!.modo).catch(() => { /* se conserva en este equipo */ })
  }
  const setNombre = (n: string) => { setNombreEstado(n); localStorage.setItem('nexora-nombre', n) }

  const resolver = async (duda: Pendiente, carpeta: string) => {
    try {
      const r = await api.decidir(duda.archivo.id, carpeta)
      avisar(r.aprendizaje_registrado ? `${duda.archivo.nombre} → ${nombrar(r.carpeta_elegida, destinos)}` : `${duda.archivo.nombre} se movió, pero no se pudo guardar el aprendizaje.`, !r.aprendizaje_registrado)
    } catch (err) { avisar(mensaje(err), true) }
    await refrescar()
  }
  const confirmarTodas = async () => {
    setMasOpen(false)
    let bien = 0
    let ultimo = ''
    // Las carpetas nuevas no se crean en bloque: cada una se pregunta.
    const seguras = dudas.filter((d) => d.carpeta_existe)
    for (const d of seguras) {
      try { await api.decidir(d.archivo.id, d.carpeta_sugerida); bien++ } catch (err) { ultimo = `${d.archivo.nombre}: ${mensaje(err)}` }
    }
    avisar(ultimo ? `${bien} confirmados. ${ultimo}` : `${bien} ${bien === 1 ? "archivo confirmado" : "archivos confirmados"}.${seguras.length < dudas.length ? " Los demás necesitan que elijas carpeta." : ""}`, !!ultimo)
    await refrescar()
  }
  const importar = async (rutas: string[]) => {
    try {
      const { aceptados, omitidos } = await api.importar(rutas)
      const n = aceptados.length
      const recibidos = n ? `${n} ${n === 1 ? 'archivo recibido' : 'archivos recibidos'}. La IA busca dónde moverlo${n === 1 ? '' : 's'}. ` : ''
      avisar(recibidos + omitidos.join('; '), omitidos.length > 0)
    } catch (err) { avisar(mensaje(err), true) }
    await refrescar()
  }
  const explorar = async () => {
    try {
      const rutas = await elegirArchivos()
      if (rutas.length) await importar(rutas)
    } catch (err) { avisar(mensaje(err), true) }
  }

  const importarRef = useRef(importar)
  importarRef.current = importar
  useEffect(() => alSoltarArchivos(setDragging, (rutas) => { setVista('dashboard'); void importarRef.current(rutas) }), [])

  const selectorCarpeta = (elegir: (c: string) => void, ocupado: boolean) =>
    <SelectorCarpeta nombre="estos archivos" destinos={destinos} ocupado={ocupado} elegir={elegir} />

  const selectedTheme = themeOptions.find((option) => option.value === theme) ?? themeOptions[2]
  const hora = new Date().getHours()
  const saludo = hora < 12 ? 'Buenos días' : hora < 19 ? 'Buenas tardes' : 'Buenas noches'
  const diferencia = resumen ? resumen.clasificados_hoy - resumen.clasificados_ayer : 0
  const titulo = vista === 'dashboard' ? `${saludo}${nombre && `, ${nombre}`}` : nav.find(([v]) => v === vista)![1]
  const conectado = !apagado

  return (
    <main className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <div className="workspace">
        <aside className="sidebar">
          <div>
            <div className="brand">
              <div className="brand-mark"><span /><span /></div>
              <div><strong>Nexora</strong><small>INTELLIGENT FILES</small></div>
            </div>
            <nav aria-label="Navegación principal">
              {nav.map(([id, label]) => (
                <button className={`nav-item ${vista === id ? 'active' : ''}`} aria-current={vista === id ? 'page' : undefined} aria-label={label} key={id} onClick={() => setVista(id)}>
                  <span className="nav-icon"><Icon name={id} /></span>
                  <span>{label}</span>
                  {id === 'inbox' && dudas.length > 0 && <b className="badge">{dudas.length}</b>}
                </button>
              ))}
            </nav>
          </div>
          <div className={`ai-status ${conectado ? '' : 'off'}`}>
            <span className="status-dot" />
            <div><small>{conectado ? 'MOTOR CONECTADO' : 'MOTOR DESCONECTADO'}</small><strong>{conectado ? (resumen?.en_proceso ? 'Analizando archivos' : resumen?.usando_respaldo ? 'IA de respaldo' : 'Activo y vigilando') : encendiendo ? 'Encendiendo…' : 'Apagado'}</strong></div>
            <span className="signal"><i/><i/><i/></span>
          </div>
        </aside>

        <section className="content">
          <header className="topbar">
            <div>
              <p className="eyebrow">CENTRO DE CONTROL</p>
              <h1>{titulo} <span>.</span></h1>
              <p className="subtitle">{subtitulos[vista]}</p>
            </div>
            <div className="top-actions">
              <div className={`system-pill ${conectado ? '' : 'off'}`}><span className="status-dot" /> {conectado ? 'Sistema activo' : 'Sin conexión'}</div>
              <div className="theme-select" ref={themeRef}>
                <button className="theme-trigger" aria-label="Cambiar tema" onClick={() => setThemeOpen(!themeOpen)} aria-expanded={themeOpen}>
                  <span className="theme-symbol">{selectedTheme.icon}</span>
                  <span><small>MODO</small>{selectedTheme.label}</span>
                  <Icon name="chevron" size={17} />
                </button>
                {themeOpen && (
                  <div className="theme-menu">
                    {themeOptions.map((option) => (
                      <button className={theme === option.value ? 'selected' : ''} key={option.value} onClick={() => cambiarTema(option.value)}>
                        <span>{option.icon}</span>{option.label}{theme === option.value && <i>✓</i>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button className="avatar" aria-label="Perfil" onClick={() => setVista('settings')}>{(nombre || 'NX').slice(0, 2).toUpperCase()}<span /></button>
            </div>
          </header>

          {!conectado && (
            <div className="banner" role="alert">
              <p>{encendiendo ? 'Encendiendo el motor de Nexora… puede tardar unos segundos.' : apagado}</p>
              <button className="boton principal" disabled={encendiendo} onClick={encender}>Encender motor</button>
            </div>
          )}

          {conectado && resumen?.usando_respaldo && (
            <div className="banner respaldo" role="status">
              <p>Se agotó la cuota de la IA principal. Nexora sigue con <strong>{resumen.motor_ia}</strong> y volverá a intentarlo en {resumen.vuelve_en < 5400 ? `${Math.max(1, Math.round(resumen.vuelve_en / 60))} min` : `${Math.round(resumen.vuelve_en / 3600)} h`}.</p>
            </div>
          )}

          {vista === 'dashboard' && <>
            <section className="metrics" aria-label="Resumen diario">
              <article className="metric-card cyan">
                <div className="metric-top"><span className="metric-icon">✓</span></div>
                <p>CLASIFICADOS HOY</p><strong>{resumen?.clasificados_hoy ?? '–'}</strong><small>{diferencia === 0 ? 'Igual que ayer' : `${Math.abs(diferencia)} ${diferencia > 0 ? 'más' : 'menos'} que ayer`}</small>
              </article>
              <article className="metric-card violet">
                <div className="metric-top"><span className="metric-icon">✦</span></div>
                <p>PRECISIÓN IA</p><strong>{resumen?.precision ?? '–'}{resumen?.precision != null && <span>%</span>}</strong><small>Archivos que no corregiste</small>
              </article>
              <article className="metric-card pink">
                <div className="metric-top"><span className="metric-icon">?</span>{dudas.length > 0 && <span className="review-label">REVISAR</span>}</div>
                <p>PENDIENTES</p><strong>{resumen?.pendientes ?? '–'}</strong><small>{resumen?.errores ? `${resumen.errores} con error (ver historial)` : 'Requieren tu decisión'}</small>
              </article>
              <article
                className={`drop-zone ${dragging ? 'dragging' : ''}`}
                onClick={explorar}
              >
                <div className="upload-icon"><Icon name="upload" size={25} /></div>
                <div><strong>Arrastra tus archivos</strong><span>Se moverán a su carpeta</span></div>
                <button aria-label="Añadir archivos" onClick={(e) => { e.stopPropagation(); void explorar() }}><Icon name="plus" size={19} /></button>
              </article>
            </section>

            <article className="panel pagina indicar">
              <div className="panel-heading"><h2>Enséñale a la IA</h2><button className="boton" onClick={() => setVista('rules')}>Ver indicaciones</button></div>
              <NuevaIndicacion avisar={avisar} selector={selectorCarpeta} />
            </article>
            <section className="dashboard-grid">
              <article className="panel doubts">
                <div className="panel-heading">
                  <div><p className="eyebrow">HUMAN-IN-THE-LOOP</p><h2>Bandeja de Dudas <span>{dudas.length}</span></h2></div>
                  <div className="theme-select" ref={masRef}>
                    <button className="more" aria-label="Más opciones" aria-expanded={masOpen} onClick={() => setMasOpen(!masOpen)}>•••</button>
                    {masOpen && (
                      <div className="theme-menu mas">
                        <button onClick={() => { setMasOpen(false); void refrescar().then(() => avisar('Datos actualizados.')) }}>Actualizar</button>
                        <button disabled={!dudas.length} onClick={confirmarTodas}>Confirmar todas</button>
                        <button onClick={() => { setMasOpen(false); setVista('inbox') }}>Ver bandeja completa</button>
                      </div>
                    )}
                  </div>
                </div>
                <p className="panel-description">La IA necesita tu criterio para clasificar estos archivos.</p>
                <div className="file-list"><ListaDudas dudas={dudas.slice(0, 2)} destinos={destinos} resolver={resolver} /></div>
                <footer className="learning-note"><span>✦</span><p><strong>Cada decisión mejora a Nexora</strong><br/>La IA recordará tus preferencias para el futuro.</p></footer>
              </article>

              <article className="panel activity">
                <div className="panel-heading">
                  <div><p className="eyebrow">PROCESAMIENTO AUTOMÁTICO</p><h2>Actividad en Vivo</h2></div>
                  {conectado && <span className="live"><i /> EN VIVO</span>}
                </div>
                <div className="timeline">
                  {eventos.slice(0, 4).map((e) => (
                    <div className="timeline-item" key={e.id}>
                      <div className="timeline-mark">{e.estado === 'error' ? '!' : e.estado === 'pendiente' ? '?' : e.estado === 'procesando' ? '…' : '✓'}</div>
                      <div className="event">
                        <div className="event-head"><strong>{e.nombre}</strong><time>{haceCuanto(e.actualizado_en)}</time></div>
                        <div className="destination"><span>→</span><strong>{e.estado === 'clasificado' || e.estado === 'corregido' ? (e.carpeta ? nombrar(e.carpeta, destinos) : 'Se dejó en su sitio') : etiquetas[e.estado]}</strong></div>
                      </div>
                    </div>
                  ))}
                  {!eventos.length && <p className="panel-description">Aún no hay actividad.</p>}
                </div>
                <button className="view-history" onClick={() => setVista('history')}>Ver historial completo <span>→</span></button>
              </article>
            </section>
          </>}

          {vista === 'inbox' && (
            <article className="panel pagina">
              <div className="panel-heading"><h2>Pendientes <span>{dudas.length}</span></h2><button className="boton" disabled={!dudas.length} onClick={confirmarTodas}>Confirmar todas</button></div>
              <div className="file-list"><ListaDudas dudas={dudas} destinos={destinos} resolver={resolver} /></div>
            </article>
          )}
          {vista === 'rules' && <Indicaciones key={String(conectado)} avisar={avisar} selector={selectorCarpeta} />}
          {vista === 'folder' && <Carpetas key={String(conectado)} avisar={avisar} />}
          {vista === 'history' && <Historial eventos={eventos} destinos={destinos} avisar={avisar} refrescar={refrescar} />}
          {vista === 'settings' && <Configuracion key={String(conectado)} nombre={nombre} setNombre={setNombre} theme={theme} cambiarTema={cambiarTema} avisar={avisar} />}
        </section>
      </div>
      {aviso && <div className={`toast ${aviso.error ? 'error' : ''}`} role="status"><span>{aviso.error ? '!' : '✓'}</span>{aviso.texto}</div>}
    </main>
  )
}

export default App
