import { useCallback, useEffect, useState } from 'react'
import { api, elegirCarpeta, type EstadoOrganizar } from './api'

const mensaje = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Organiza archivos que ya estaban en una carpeta: lote a lote, y solo mueve lo que el usuario aprueba. */
export function Organizar({ inicial, avisar, alCambiar }: {
  inicial: { ruta: string; recursiva: boolean } | null
  avisar: (texto: string, error?: boolean) => void
  alCambiar: () => void
}) {
  const [estado, setEstado] = useState<EstadoOrganizar | null>(null)
  const [elegidos, setElegidos] = useState<Record<string, boolean>>({})
  const [ocupado, setOcupado] = useState(false)

  const aplicar = useCallback((e: EstadoOrganizar) => {
    setEstado(e)
    // Se marcan por defecto los que van a una carpeta que ya existe y no son copias.
    setElegidos(Object.fromEntries(e.lote.map((d) => [d.archivo.id, d.carpeta_existe && !d.duplicado_de])))
  }, [])
  const accion = async (llamada: Promise<EstadoOrganizar>, exito?: (e: EstadoOrganizar) => string) => {
    setOcupado(true)
    try {
      const e = await llamada
      aplicar(e)
      if (e.error) avisar(e.error, true)
      else if (exito) avisar(exito(e))
      alCambiar()
    } catch (err) { avisar(mensaje(err), true) } finally { setOcupado(false) }
  }

  useEffect(() => { void api.estadoOrganizar().then(aplicar).catch(() => {}) }, [aplicar])
  useEffect(() => { if (inicial) void accion(api.prepararOrganizar(inicial.ruta, inicial.recursiva)) }, [inicial]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (estado?.estado !== 'analizando') return
    const id = window.setInterval(() => void api.estadoOrganizar().then(aplicar).catch(() => {}), 2000)
    return () => window.clearInterval(id)
  }, [estado?.estado, aplicar])

  const otra = async () => {
    const ruta = await elegirCarpeta('¿Qué carpeta quieres organizar?').catch(() => null)
    if (ruta) void accion(api.prepararOrganizar(ruta, false))
  }

  if (!estado?.activa) {
    return (
      <article className="panel pagina">
        <h2>Organizar lo que ya tienes</h2>
        <p className="panel-description">Nexora solo organiza sola los archivos nuevos. Para los que ya tienes, elige una carpeta: la IA propondrá dónde va cada archivo, por lotes, y tú decides qué se mueve.</p>
        <button className="boton principal" disabled={ocupado} onClick={otra}>Organizar una carpeta…</button>
      </article>
    )
  }

  const aprobados = Object.fromEntries(estado.lote.filter((d) => elegidos[d.archivo.id]).map((d) => [d.archivo.id, d.carpeta_sugerida]))
  const n = Object.keys(aprobados).length
  const cerrar = () => void accion(api.cancelarOrganizar().then(() => ({ activa: false }) as EstadoOrganizar))

  return (
    <article className="panel pagina organizar">
      <div className="panel-heading">
        <h2>Organizando <span>{estado.hechos} de {estado.total}</span></h2>
        <button className="boton" disabled={ocupado} onClick={cerrar}>{estado.estado === 'terminada' ? 'Cerrar' : 'Detener'}</button>
      </div>
      <p className="ruta">{estado.carpeta}</p>

      {estado.estado === 'lista' && (estado.total === 0
        ? <p className="panel-description">No hay archivos sin organizar en esta carpeta.</p>
        : <>
          <p className="panel-description">Quedan <strong>{estado.restantes}</strong> archivos. Cada uno usa una consulta a la IA; se analizan de 10 en 10 y nada se mueve sin tu aprobación.</p>
          <button className="boton principal" disabled={ocupado} onClick={() => void accion(api.siguienteOrganizar())}>Analizar {Math.min(10, estado.restantes)} archivos</button>
        </>)}

      {estado.estado === 'analizando' && <p className="panel-description">La IA está analizando el lote… {estado.lote.length} listos.</p>}

      {estado.estado === 'revisar' && <>
        <p className="panel-description">Marca los que quieres mover. Los demás quedarán en la Bandeja de Dudas.</p>
        <div className="lista">
          {estado.lote.map((d) => (
            <label className="item lote" key={d.archivo.id}>
              <input type="checkbox" aria-label={`Mover ${d.archivo.nombre}`} checked={!!elegidos[d.archivo.id]} onChange={(e) => setElegidos({ ...elegidos, [d.archivo.id]: e.target.checked })} />
              <div>
                <strong>{d.archivo.nombre}</strong>
                <p>→ {d.carpeta_etiqueta || 'Sin carpeta'}{!d.carpeta_existe && d.carpeta_sugerida && ' (carpeta nueva)'} · {Math.round(d.confianza * 100)}%</p>
                {d.duplicado_de && <p className="aviso-error">Copia exacta de {d.duplicado_de.nombre}</p>}
              </div>
            </label>
          ))}
          {estado.resueltos.map((a) => <div className="item" key={a.id}><div><strong>{a.nombre}</strong><p>{a.estado === 'error' ? 'No se pudo analizar' : 'Se deja en su sitio por tu indicación'}</p></div></div>)}
        </div>
        <div className="fila">
          <button className="boton principal" disabled={ocupado} onClick={() => void accion(api.aprobarOrganizar(aprobados), () => `${n} ${n === 1 ? 'archivo movido' : 'archivos movidos'}.`)}>{n ? `Mover ${n} y continuar` : 'Dejar todo en la Bandeja y continuar'}</button>
        </div>
      </>}

      {estado.estado === 'terminada' && <p className="panel-description">Listo. Lo que no aprobaste está en la Bandeja de Dudas.</p>}
    </article>
  )
}
