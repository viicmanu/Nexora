import { useCallback, useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { api, abrirAjustesWifi, escucharEventos, permitirFirewall, type EstadoTelefono } from './api'

const mensaje = (e: unknown) => (e instanceof Error ? e.message : String(e))
const fecha = (iso: string | null) => (iso ? new Date(iso).toLocaleString('es') : 'Nunca')
const plataformas: Record<string, string> = { android: 'Android', ios: 'iOS' }

/** Configuración → Teléfono vinculado: estado en tiempo real, QR para vincular, renombrar, desvincular y reemplazar. */
export function TelefonoVinculado({ avisar }: { avisar: (texto: string, error?: boolean) => void }) {
  const [estado, setEstado] = useState<EstadoTelefono | null>(null)
  const [qr, setQr] = useState<{ imagen: string; expira: string; texto: string } | null>(null)
  const [nombre, setNombre] = useState('')
  const [editando, setEditando] = useState(false)
  const [confirmar, setConfirmar] = useState<'' | 'desvincular' | 'reemplazar'>('')
  const [maxMb, setMaxMb] = useState('')
  const [fallos, setFallos] = useState<{ archivo: string; recibido: string; tipo: string; primera_linea: string }[]>([])
  const [fallo, setFallo] = useState<{ archivo: string; texto: string } | null>(null)

  const cargar = useCallback(async () => {
    try {
      const e = await api.telefono()
      setEstado(e)
      setMaxMb((m) => m || String(e.max_subida_mb))
      if (e.telefono) setQr(null)
      setFallos(await api.fallosTelefono().catch(() => []))
    } catch (err) { avisar(mensaje(err), true) }
  }, [avisar])

  useEffect(() => {
    void cargar()
    // En tiempo real: vinculación, desvinculación y paso a en línea / desconectado.
    const parar = escucharEventos((ev) => {
      if (ev.tipo === 'vinculado') avisar(`${ev.nombre} se vinculó a este PC.`)
      if (ev.tipo === 'fallo_telefono') avisar('El teléfono envió el informe de un cierre inesperado.', true)
      if (['vinculado', 'desvinculado', 'telefono', 'fallo_telefono'].includes(String(ev.tipo))) void cargar()
    })
    const id = window.setInterval(() => void cargar(), 30000) // el paso a "desconectado" puede tardar hasta 90 s
    return () => { parar(); window.clearInterval(id) }
  }, [cargar, avisar])

  const generarQr = async (reemplazar = false) => {
    try {
      const c = await api.codigoVinculacion(reemplazar)
      setQr({ imagen: await QRCode.toDataURL(c.contenido_qr, { margin: 1, width: 240, errorCorrectionLevel: 'M' }), expira: c.expira_en, texto: c.contenido_qr })
      setConfirmar('')
      await cargar()
    } catch (err) { avisar(mensaje(err), true) }
  }
  const desvincular = async () => {
    if (!estado?.telefono) return
    try {
      await api.desvincular(estado.telefono.id)
      avisar('Teléfono desvinculado. Ya no puede conectarse a este PC.')
      setConfirmar('')
      await cargar()
    } catch (err) { avisar(mensaje(err), true) }
  }
  const renombrar = async () => {
    if (!estado?.telefono || !nombre.trim()) return
    try { await api.renombrarDispositivo(estado.telefono.id, nombre.trim()); setEditando(false); await cargar() } catch (err) { avisar(mensaje(err), true) }
  }
  const guardarMax = async () => {
    try { await api.guardarMaxSubida(Number(maxMb)); avisar('Tamaño máximo guardado.') } catch (err) { avisar(mensaje(err), true) }
  }

  const t = estado?.telefono
  const privada = estado?.red.redes.some((r) => r.privada) ?? false

  return (
    <article className="panel pagina telefono">
      <h2>Teléfono vinculado</h2>
      {estado && !estado.red.lan && <p className="aviso-error">La conexión con el teléfono está desactivada (NEXORA_LAN_HABILITADA).</p>}
      {estado?.red.lan && !privada && (
        <div className="respuesta propuesta">
          <p><strong>Tu red Wi-Fi está marcada como pública.</strong> Por seguridad, Nexora solo acepta el teléfono en redes privadas.</p>
          <p className="ruta">En Windows: Configuración → Red e Internet → Wi-Fi → tu red → Tipo de perfil de red: Privada.</p>
          <div className="fila"><button className="boton" onClick={() => void abrirAjustesWifi()}>Abrir ajustes de Wi-Fi</button><button className="boton" onClick={() => void cargar()}>Comprobar de nuevo</button></div>
        </div>
      )}

      {t ? (
        <div className="item tarjeta-telefono">
          <span className={`punto ${t.en_linea ? 'en-linea' : ''}`} aria-hidden="true" />
          <div>
            {editando
              ? <form className="fila" onSubmit={(e) => { e.preventDefault(); void renombrar() }}><input aria-label="Nombre del teléfono" autoFocus maxLength={80} value={nombre} onChange={(e) => setNombre(e.target.value)} /><button className="boton principal">Guardar</button><button type="button" className="boton" onClick={() => setEditando(false)}>Cancelar</button></form>
              : <strong>{t.nombre}</strong>}
            <p>{t.en_linea ? 'En línea' : 'Desconectado'} · {plataformas[t.plataforma ?? ''] ?? 'Teléfono'}{t.ultima_ip && ` · ${t.ultima_ip}`}</p>
            <p>Vinculado el {fecha(t.vinculado_en)} · Último acceso: {fecha(t.ultimo_acceso)}</p>
          </div>
          {!editando && <div className="acciones">
            <button className="boton" onClick={() => { setNombre(t.nombre); setEditando(true) }}>Renombrar</button>
            <button className="boton" onClick={() => setConfirmar('reemplazar')}>Reemplazar</button>
            <button className="boton" onClick={() => setConfirmar('desvincular')}>Desvincular</button>
          </div>}
        </div>
      ) : !qr && (
        <>
          <ol className="pasos">
            <li>Conecta el teléfono a la misma red Wi-Fi que este PC.</li>
            <li>Abre Nexora en el teléfono y toca «Vincular con un PC».</li>
            <li>Escanea el código QR que aparecerá aquí.</li>
          </ol>
          <button className="boton principal" disabled={!estado?.red.lan} onClick={() => void generarQr()}>Mostrar código QR</button>
        </>
      )}

      {confirmar && t && (
        <div className="respuesta propuesta">
          <p>{confirmar === 'desvincular'
            ? `¿Desvincular ${t.nombre}? Dejará de poder conectarse y borrará su lista y su cola de subidas.`
            : `¿Reemplazar ${t.nombre}? Se desvinculará ahora y aparecerá un QR nuevo para el otro teléfono.`}</p>
          <div className="fila">
            <button className="boton principal" onClick={() => void (confirmar === 'desvincular' ? desvincular() : generarQr(true))}>{confirmar === 'desvincular' ? 'Sí, desvincular' : 'Sí, reemplazar'}</button>
            <button className="boton" onClick={() => setConfirmar('')}>Cancelar</button>
          </div>
        </div>
      )}

      {qr && !t && (
        <div className="qr">
          <img src={qr.imagen} alt="Código QR para vincular el teléfono" width={240} height={240} />
          <div>
            <p>Escanéalo con Nexora en el teléfono. Caduca a las {new Date(qr.expira).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })} y solo sirve una vez.</p>
            <p className="ruta">Direcciones: {estado?.red.redes.map((r) => `${r.ip}:${estado.red.puerto}`).join(', ') || 'sin red local'}</p>
            <div className="fila"><button className="boton" onClick={() => void navigator.clipboard.writeText(qr.texto).then(() => avisar('Código copiado. Pégalo en el teléfono si no puede escanear.'))}>Copiar código</button><button className="boton" onClick={() => void generarQr()}>Generar otro</button><button className="boton" onClick={() => setQr(null)}>Cancelar</button></div>
          </div>
        </div>
      )}

      {estado?.app_movil && <p className="panel-description">App del teléfono publicada: versión {estado.app_movil.codigo} ({(estado.app_movil.tamano / 1048576).toFixed(1)} MB). El teléfono la descargará solo y te pedirá instalarla.</p>}
      {fallos.length > 0 && (
        <div className="fallos">
          <h3>Fallos del teléfono <span>{fallos.length}</span></h3>
          <div className="lista">
            {fallos.slice(0, 10).map((f) => (
              <div className="item" key={f.archivo}>
                <div><strong>{f.tipo.replace(/_/g, ' ')}</strong><p>{f.recibido.replace('T', ' ')} · {f.primera_linea}</p></div>
                <button className="boton" onClick={async () => setFallo({ archivo: f.archivo, texto: (await api.verFallo(f.archivo)).texto })}>Ver detalle</button>
              </div>
            ))}
          </div>
          {fallo && (
            <div className="respuesta">
              <div className="fila"><button className="boton" onClick={() => void navigator.clipboard.writeText(fallo.texto).then(() => avisar('Informe copiado.'))}>Copiar</button><button className="boton" onClick={() => setFallo(null)}>Cerrar</button></div>
              <pre className="informe">{fallo.texto}</pre>
            </div>
          )}
        </div>
      )}
      <div className="fila ajustes-telefono">
        <label className="casilla">Tamaño máximo de lo que sube el teléfono: <input aria-label="Tamaño máximo en MB" type="number" min={1} max={4096} value={maxMb} onChange={(e) => setMaxMb(e.target.value)} /> MB</label>
        <button className="boton" onClick={() => void guardarMax()}>Guardar</button>
        <button className="boton" title="Pide permiso de administrador para abrir el puerto solo en redes privadas" onClick={() => void permitirFirewall(estado?.red.puerto ?? 8767).then(() => avisar('Si aceptaste el permiso, el firewall ya deja pasar al teléfono en redes privadas.'), (e) => avisar(mensaje(e), true))}>Permitir en el firewall (redes privadas)</button>
      </div>
    </article>
  )
}
