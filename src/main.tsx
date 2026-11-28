import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import AppMovil from './movil/AppMovil'
import { CapturaDeFallos, capturarErroresGlobales } from './movil/fallos'
import './index.css'

// En Android/iOS se abre la app del teléfono. En el PC se puede probar con VITE_MODO=movil o ?movil.
const esMovil =
  /Android|iPhone|iPad/i.test(navigator.userAgent) ||
  import.meta.env.VITE_MODO === 'movil' ||
  new URLSearchParams(location.search).has('movil')

if (esMovil) {
  capturarErroresGlobales()
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  document.documentElement.dataset.theme = localStorage.getItem('nexora-theme') === 'light' || (!media.matches && localStorage.getItem('nexora-theme') !== 'dark') ? 'light' : 'dark'
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>{esMovil ? <CapturaDeFallos><AppMovil /></CapturaDeFallos> : <App />}</React.StrictMode>,
)
