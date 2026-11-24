import { useEffect, useRef, useState, type ReactNode } from 'react'

type Theme = 'light' | 'dark' | 'system'

const icons: Record<string, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
  inbox: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></>,
  folder: <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  upload: <><path d="M12 16V3M7 8l5-5 5 5"/><path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/></>,
  chevron: <path d="m7 10 5 5 5-5"/>,
  plus: <path d="M12 5v14M5 12h14"/>,
}

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{icons[name]}</svg>
}

const themeOptions: { value: Theme; label: string; icon: string }[] = [
  { value: 'light', label: 'Claro', icon: '☀' },
  { value: 'dark', label: 'Oscuro', icon: '☾' },
  { value: 'system', label: 'Automático (Sistema)', icon: '◉' },
]

const nav = [
  ['dashboard', 'Dashboard'],
  ['inbox', 'Bandeja de Dudas'],
  ['folder', 'Carpetas Vigiladas'],
  ['history', 'Historial de Aprendizaje'],
  ['settings', 'Configuración'],
]

const initialFiles = [
  { type: 'PDF', name: 'Factura_Noviembre.pdf', summary: 'Documento de servicios y consumo mensual', path: '/Finanzas/Hogar', confidence: 62, kind: 'pdf' },
  { type: 'DOCX', name: 'Contrato_Servicios.docx', summary: 'Acuerdo legal de prestación de servicios', path: '/Legal/Contratos', confidence: 54, kind: 'docx' },
]

const activity = [
  ['Recibo_Agua.pdf', '/Finanzas/Servicios', 'hace 2 min'],
  ['Foto_Viaje.jpg', '/Imágenes/Viajes', 'hace 8 min'],
  ['Proyecto_Final.zip', '/Trabajo/Proyectos', 'hace 14 min'],
  ['Nómina_Octubre.pdf', '/Finanzas/Trabajo', 'hace 21 min'],
]

function App() {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem('nexora-theme') as Theme) || 'system')
  const [themeOpen, setThemeOpen] = useState(false)
  const [files, setFiles] = useState(initialFiles)
  const [notice, setNotice] = useState('')
  const [dragging, setDragging] = useState(false)
  const themeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => document.documentElement.dataset.theme = theme === 'system' ? (media.matches ? 'dark' : 'light') : theme
    apply()
    localStorage.setItem('nexora-theme', theme)
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [theme])

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!themeRef.current?.contains(event.target as Node)) setThemeOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const confirmFile = (name: string) => {
    setFiles((current) => current.filter((file) => file.name !== name))
    setNotice(`${name} se clasificó correctamente`)
    window.setTimeout(() => setNotice(''), 2600)
  }

  const selectedTheme = themeOptions.find((option) => option.value === theme)!

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
              {nav.map(([icon, label], index) => (
                <button className={`nav-item ${index === 0 ? 'active' : ''}`} key={label}>
                  <span className="nav-icon"><Icon name={icon} /></span>
                  <span>{label}</span>
                  {index === 1 && <b className="badge">3</b>}
                </button>
              ))}
            </nav>
          </div>
          <div className="ai-status">
            <span className="status-dot" />
            <div><small>IA CONECTADA</small><strong>Activa y aprendiendo</strong></div>
            <span className="signal"><i/><i/><i/></span>
          </div>
        </aside>

        <section className="content">
          <header className="topbar">
            <div>
              <p className="eyebrow">CENTRO DE CONTROL</p>
              <h1>Buenos días, Alex <span>.</span></h1>
              <p className="subtitle">Tu espacio digital está bajo control.</p>
            </div>
            <div className="top-actions">
              <div className="system-pill"><span className="status-dot" /> Sistema activo</div>
              <div className="theme-select" ref={themeRef}>
                <button className="theme-trigger" onClick={() => setThemeOpen(!themeOpen)} aria-expanded={themeOpen}>
                  <span className="theme-symbol">{selectedTheme.icon}</span>
                  <span><small>MODO</small>{selectedTheme.label}</span>
                  <Icon name="chevron" size={17} />
                </button>
                {themeOpen && (
                  <div className="theme-menu">
                    {themeOptions.map((option) => (
                      <button className={theme === option.value ? 'selected' : ''} key={option.value} onClick={() => { setTheme(option.value); setThemeOpen(false) }}>
                        <span>{option.icon}</span>{option.label}{theme === option.value && <i>✓</i>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button className="avatar" aria-label="Perfil de Alex">AS<span /></button>
            </div>
          </header>

          <section className="metrics" aria-label="Resumen diario">
            <article className="metric-card cyan">
              <div className="metric-top"><span className="metric-icon">✓</span><span className="trend">↗ 12%</span></div>
              <p>CLASIFICADOS HOY</p><strong>142</strong><small>18 más que ayer</small>
            </article>
            <article className="metric-card violet">
              <div className="metric-top"><span className="metric-icon">✦</span><span className="trend">↗ 2.4%</span></div>
              <p>PRECISIÓN IA</p><strong>94<span>%</span></strong><small>Excelente rendimiento</small>
            </article>
            <article className="metric-card pink">
              <div className="metric-top"><span className="metric-icon">?</span><span className="review-label">REVISAR</span></div>
              <p>PENDIENTES</p><strong>3</strong><small>Requieren tu decisión</small>
            </article>
            <article
              className={`drop-zone ${dragging ? 'dragging' : ''}`}
              onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => { event.preventDefault(); setDragging(false); setNotice('Archivos recibidos. La IA está analizándolos.') }}
            >
              <div className="upload-icon"><Icon name="upload" size={25} /></div>
              <div><strong>Arrastra tus archivos</strong><span>o haz clic para explorar</span></div>
              <button aria-label="Añadir archivos"><Icon name="plus" size={19} /></button>
            </article>
          </section>

          <section className="dashboard-grid">
            <article className="panel doubts">
              <div className="panel-heading">
                <div><p className="eyebrow">HUMAN-IN-THE-LOOP</p><h2>Bandeja de Dudas <span>{files.length || 0}</span></h2></div>
                <button className="more" aria-label="Más opciones">•••</button>
              </div>
              <p className="panel-description">La IA necesita tu criterio para clasificar estos archivos.</p>

              <div className="file-list">
                {files.length ? files.map((file) => (
                  <div className="file-card" key={file.name}>
                    <div className={`file-icon ${file.kind}`}><span>{file.type}</span></div>
                    <div className="file-info">
                      <div className="file-title"><div><strong>{file.name}</strong><p>{file.summary}</p></div><span className="confidence">{file.confidence}% segura</span></div>
                      <div className="suggestion"><small>CARPETA SUGERIDA</small><strong><Icon name="folder" size={15} /> {file.path}</strong></div>
                      <div className="file-actions">
                        <button className="confirm" onClick={() => confirmFile(file.name)}>Confirmar</button>
                        <button className="change">Cambiar carpeta <Icon name="chevron" size={15} /></button>
                        <button className="create"><Icon name="plus" size={15} /> Nueva</button>
                      </div>
                    </div>
                  </div>
                )) : <div className="empty-state"><span>✓</span><strong>Todo está en orden</strong><p>No hay archivos pendientes de revisión.</p></div>}
              </div>
              <footer className="learning-note"><span>✦</span><p><strong>Cada decisión mejora a Nexora</strong><br/>La IA recordará tus preferencias para el futuro.</p></footer>
            </article>

            <article className="panel activity">
              <div className="panel-heading">
                <div><p className="eyebrow">PROCESAMIENTO AUTOMÁTICO</p><h2>Actividad en Vivo</h2></div>
                <span className="live"><i /> EN VIVO</span>
              </div>
              <div className="timeline">
                {activity.map(([name, path, time]) => (
                  <div className="timeline-item" key={name}>
                    <div className="timeline-mark">✓</div>
                    <div className="event">
                      <div className="event-head"><strong>{name}</strong><time>{time}</time></div>
                      <div className="destination"><span>→</span><strong>{path}</strong></div>
                    </div>
                  </div>
                ))}
              </div>
              <button className="view-history">Ver historial completo <span>→</span></button>
            </article>
          </section>
        </section>
      </div>
      {notice && <div className="toast"><span>✓</span>{notice}</div>}
    </main>
  )
}

export default App
