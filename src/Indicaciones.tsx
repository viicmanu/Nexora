import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { api, type Indicacion, type Interpretacion } from './api'

const mensaje = (e: unknown) => (e instanceof Error ? e.message : String(e))
const efectos: Record<Indicacion['accion'], string> = {
  mover: 'Mover', preguntar: 'Preguntarte siempre', no_mover: 'Dejar en su sitio', guia: 'Tener en cuenta',
}

interface Props {
  avisar: (texto: string, error?: boolean) => void
  /** Dibuja el selector de carpetas de la app. */
  selector: (elegir: (carpeta: string) => void, ocupado: boolean) => ReactNode
  alGuardar?: () => void
}

/** El usuario escribe una indicación, la IA dice cómo la entendió y se guarda solo si la confirma. */
export function NuevaIndicacion({ avisar, selector, alGuardar }: Props) {
  const [texto, setTexto] = useState('')
  const [leida, setLeida] = useState<Interpretacion | null>(null)
  const [fallo, setFallo] = useState('')
  const [eligiendo, setEligiendo] = useState(false)
  const [ocupado, setOcupado] = useState<'' | 'pensando' | 'guardando'>('')

  const limpiar = () => { setLeida(null); setFallo(''); setEligiendo(false) }
  const enviar = async (e: FormEvent) => {
    e.preventDefault()
    setOcupado('pensando')
    limpiar()
    try {
      const r = await api.interpretarIndicacion(texto.trim())
      setLeida(r)
      setEligiendo(r.entendida && r.accion === 'mover' && !r.carpeta)
    } catch (err) { setFallo(mensaje(err)) } finally { setOcupado('') }
  }
  const guardar = async (datos: { resumen: string; accion: Indicacion['accion']; carpeta: string | null; crear_carpeta: boolean }) => {
    setOcupado('guardando')
    try {
      await api.guardarIndicacion({ texto: texto.trim(), ...datos })
      avisar('Indicación guardada. La IA la aplicará a los próximos archivos.')
      setTexto('')
      limpiar()
      alGuardar?.()
    } catch (err) { avisar(mensaje(err), true) } finally { setOcupado('') }
  }
  const confirmar = (carpeta: string | null, crear = false) =>
    // Si el usuario cambia la carpeta, el resumen de la IA ya no vale: manda su texto.
    void guardar({ resumen: carpeta === leida!.carpeta ? leida!.resumen : texto.trim(), accion: leida!.accion, carpeta, crear_carpeta: crear })

  return (
    <div className="corregir">
      <form onSubmit={enviar}>
        <textarea
          aria-label="Indicación para la IA" rows={2} maxLength={400} disabled={!!ocupado} value={texto}
          onChange={(e) => { setTexto(e.target.value); limpiar() }}
          placeholder="Ejemplo: los recibos de agua y luz van en Documentos/Hogar · No muevas los instaladores"
        />
        <button className="boton principal" disabled={!!ocupado || texto.trim().length < 5}>{ocupado === 'pensando' ? 'Pensando…' : 'Enseñar a la IA'}</button>
      </form>
      {fallo && (
        <div className="respuesta propuesta">
          <p>La IA no pudo leerla ahora: {fallo}</p>
          <div className="fila">
            <button className="boton principal" disabled={!!ocupado} onClick={() => void guardar({ resumen: '', accion: 'guia', carpeta: null, crear_carpeta: false })}>Guardar tal como la escribí</button>
            <button className="boton" onClick={limpiar}>Cancelar</button>
          </div>
        </div>
      )}
      {leida && (
        <div className="respuesta propuesta">
          {!leida.entendida ? <>
            <p>No la entendí bien. {leida.resumen}</p>
            <div className="fila">
              <button className="boton principal" onClick={limpiar}>Escribirla de nuevo</button>
              <button className="boton" disabled={!!ocupado} onClick={() => void guardar({ resumen: '', accion: 'guia', carpeta: null, crear_carpeta: false })}>Guardar tal como la escribí</button>
            </div>
          </> : <>
            <p><strong>Entendí:</strong> {leida.resumen}</p>
            <p className="ruta">{efectos[leida.accion]}{leida.carpeta_etiqueta && `: ${leida.carpeta_etiqueta}${leida.carpeta_existe ? '' : ' (carpeta nueva)'}`}</p>
            {eligiendo && <p>¿A qué carpeta van?</p>}
            <div className="fila">
              {!eligiendo && <button className="boton principal" disabled={!!ocupado} onClick={() => confirmar(leida.carpeta, !leida.carpeta_existe)}>{leida.accion === 'mover' && !leida.carpeta_existe ? 'Sí, crear carpeta y guardar' : 'Sí, guardar'}</button>}
              {leida.accion === 'mover' && !eligiendo && <button className="boton" disabled={!!ocupado} onClick={() => setEligiendo(true)}>Elegir otra carpeta</button>}
              <button className="boton" disabled={!!ocupado} onClick={limpiar}>Cancelar</button>
            </div>
            {eligiendo && selector((c) => confirmar(c), !!ocupado)}
          </>}
        </div>
      )}
    </div>
  )
}

export function Indicaciones(props: Props) {
  const { avisar } = props
  const [lista, setLista] = useState<Indicacion[] | null>(null)
  const cargar = useCallback(() => api.indicaciones().then(setLista).catch((e) => avisar(mensaje(e), true)), [avisar])
  useEffect(() => { void cargar() }, [cargar])
  const cambiar = async (accion: Promise<unknown>, texto: string) => {
    try { await accion; avisar(texto); await cargar() } catch (err) { avisar(mensaje(err), true) }
  }

  return (
    <div className="historial">
      <article className="panel pagina">
        <h2>Enséñale a la IA</h2>
        <p className="panel-description">Escribe cómo quieres organizar tus archivos. La IA te dirá qué entendió y lo aplicará a los próximos.</p>
        <NuevaIndicacion {...props} alGuardar={() => void cargar()} />
      </article>
      <article className="panel pagina">
        <h2>Lo que le has enseñado <span>{lista?.length ?? 0}</span></h2>
        <div className="lista">
          {lista?.map((i) => (
            <div className={`item ${i.activa ? '' : 'inactiva'}`} key={i.id} data-indicacion={i.texto}>
              <span className="metric-icon">✦</span>
              <div>
                <strong>{i.texto}</strong>
                {i.resumen !== i.texto && <p>{i.resumen}</p>}
                <p>{efectos[i.accion]}{i.carpeta_etiqueta && `: ${i.carpeta_etiqueta}`} · {i.activa ? (i.usos === 1 ? 'usada 1 vez' : `usada ${i.usos} veces`) : 'en pausa'}</p>
                {i.accion === 'mover' && !i.carpeta_existe && <p className="aviso-error">La carpeta ya no existe: te preguntaré antes de mover.</p>}
              </div>
              <button className="boton" onClick={() => void cambiar(api.activarIndicacion(i.id, !i.activa), i.activa ? 'Indicación en pausa.' : 'Indicación activada.')}>{i.activa ? 'Pausar' : 'Activar'}</button>
              <button className="boton" onClick={() => void cambiar(api.borrarIndicacion(i.id), 'Indicación borrada.')}>Borrar</button>
            </div>
          ))}
          {lista?.length === 0 && <p className="panel-description">Aún no hay indicaciones.</p>}
        </div>
      </article>
    </div>
  )
}
