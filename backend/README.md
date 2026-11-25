# Backend de Nexora (FastAPI)

Servicio local que vigila carpetas, clasifica archivos con IA, mantiene un índice buscable (SQLite + ChromaDB) y expone una API REST para la app de escritorio (Tauri) y, en la Fase 2, para la app móvil.

## Puesta en marcha

```powershell
cd backend
py -3.14 -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"      # añade ,openai o ,gemini si usas esos proveedores
copy .env.example .env                        # y rellena la clave del proveedor y las carpetas
.\.venv\Scripts\python -m nexora              # http://127.0.0.1:8765  (documentación en /docs)
.\.venv\Scripts\python -m pytest              # pruebas, sin red
```

La primera ejecución descarga el modelo de embeddings de ChromaDB (~80 MB, en `~/.cache/chroma`).

## Arquitectura

```
backend/
├── nexora/
│   ├── __main__.py              arranque (uvicorn) y carga de .env
│   ├── main.py                  create_app(): middlewares, rutas, ciclo de vida
│   ├── config.py                ajustes NEXORA_* (pydantic-settings)
│   ├── servicios.py             construye y conecta todos los servicios
│   │
│   ├── core_ia/                 ── motor de IA ──
│   │   ├── llm/                 patrón Adapter
│   │   │   ├── base.py          AdaptadorLLM (generar_json / generar_texto) y ErrorLLM
│   │   │   ├── anthropic_adapter.py · openai_adapter.py · gemini_adapter.py · ollama_adapter.py
│   │   │   └── __init__.py      crear_adaptador(settings)
│   │   ├── extraccion.py        texto de PDF, DOCX y texto plano
│   │   ├── vector_store.py      ChromaDB: colecciones "archivos" y "aprendizaje"
│   │   ├── clasificador.py      LLM + ajuste por decisiones previas del usuario
│   │   ├── busqueda.py          búsqueda híbrida FTS5 + semántica (Reciprocal Rank Fusion)
│   │   ├── explicador.py        lee el archivo del PC y lo explica con el LLM
│   │   ├── aprendizaje.py       feedback: mover, registrar y guardar el embedding corregido
│   │   ├── carpetas.py          validación de carpetas y movimiento sin sobrescribir
│   │   └── prompts.py           prompts y esquema JSON de clasificación
│   │
│   ├── file_watcher/            ── vigilancia en tiempo real ──
│   │   ├── watcher.py           watchdog → asyncio, debounce, espera a que termine la copia
│   │   └── indexador.py         pipeline: hash → texto → clasificar → mover o dejar pendiente → indexar
│   │
│   ├── api_desktop_mobile/      ── API REST ──
│   │   ├── esquemas.py          modelos Pydantic de entrada/salida
│   │   ├── dependencias.py      servicios, dispositivo actual y permisos
│   │   └── rutas/               archivos.py · aprendizaje.py · ajustes.py · dispositivos.py
│   │
│   ├── security/                ── seguridad ──
│   │   ├── middleware.py        exige token en /api/* (salvo /api/health y /api/devices/pair)
│   │   ├── tokens.py            tokens por dispositivo (solo se guarda su hash) y permisos
│   │   └── emparejamiento.py    códigos QR de un solo uso para la app móvil (Fase 2)
│   │
│   └── db/
│       ├── schema.sql           esquema SQLite
│       ├── database.py          conexión
│       └── repositorio.py       consultas
└── tests/test_api.py
```

## Flujo de clasificación

1. `watchdog` detecta un archivo nuevo en una carpeta vigilada (se ignoran temporales como `.crdownload` y lo que ya está en la raíz organizada).
2. Se espera a que termine de copiarse, se calcula su hash y se extrae el texto.
3. Se buscan en ChromaDB los ejemplos del usuario más parecidos y se envían al LLM junto con las carpetas existentes.
4. El LLM devuelve carpeta, confianza, resumen ejecutivo, palabras clave y alternativas (JSON con esquema).
5. Ajuste por aprendizaje: si los ejemplos del usuario coinciden, sube la confianza; si un ejemplo casi idéntico (similitud ≥ 0,92) dice otra carpeta, la baja a 0,60 para que decida el usuario.
6. Si la confianza es ≥ `NEXORA_UMBRAL_CONFIANZA` (0,85) **y la carpeta ya existe** (en Documentos, Escritorio, Descargas, Imágenes, Música, Videos o la raíz de Nexora; configurable con `NEXORA_ZONAS_BUSQUEDA`), el archivo original se mueve allí. Si no, queda en `/api/pending`: las carpetas nuevas solo se crean si el usuario lo acepta.
7. Se guarda en SQLite (ruta, resumen, palabras clave) y su embedding en ChromaDB.

## Esquema SQLite

Definido en [nexora/db/schema.sql](nexora/db/schema.sql).

| Tabla | Para qué |
|---|---|
| `archivos_indexados` | Un registro por archivo: `ruta_original`, `ruta_actual`, `categoria`, `resumen_ejecutivo`, `palabras_clave` (JSON), `confianza`, `estado` (procesando · pendiente · clasificado · corregido · error), hash y modelo usado. |
| `archivos_fts` | Índice FTS5 (nombre, resumen, palabras clave, ruta) sincronizado por triggers; sin distinguir tildes. |
| `clasificaciones_pendientes` | Bandeja de dudas: carpeta sugerida, confianza, alternativas y razonamiento. Una abierta por archivo como máximo. |
| `feedback_aprendizaje` | Cada decisión del usuario; su `id` es también el id del ejemplo en ChromaDB. |
| `dispositivos_vinculados` | Escritorio y móviles: hash del token, permisos, clave pública (Fase 2), estado, último acceso. |
| `codigos_emparejamiento` | Códigos QR de un solo uso con caducidad. |
| `carpetas_vigiladas`, `ajustes` | Configuración persistente (carpetas y tema). |

## API

Todas las rutas `/api/*` piden `Authorization: Bearer <token>`, salvo `/api/health` y `/api/devices/pair`.

| Método y ruta | Permiso | Descripción |
|---|---|---|
| `GET /api/files/search?q=&limite=&carpeta=` | `files:search` | Búsqueda por nombre y semántica; devuelve la ruta exacta en el PC. |
| `POST /api/files/{id}/explain` | `files:explain` | `{"pregunta": "...", "detalle": "breve"\|"detallado"}`: lee el archivo y responde con el LLM. |
| `GET /api/pending` | `files:organize` | Bandeja de dudas. |
| `POST /api/feedback` | `files:organize` | `{"archivo_id", "carpeta_elegida"}`: mueve el archivo y guarda el ejemplo corregido. |
| `GET / PUT /api/settings/theme` | — / `settings:write` | Modo (`claro`, `oscuro`, `auto`), modo efectivo según el SO y tokens de color. |
| `GET /api/devices`, `POST /api/devices/pairing-code`, `DELETE /api/devices/{id}` | `devices:manage` | Gestión de dispositivos y códigos QR. |
| `POST /api/devices/pair` | pública | El móvil canjea el código QR por su token. |

El escritorio tiene todos los permisos. Un móvil emparejado solo tiene `files:search` y `files:explain`.

## Seguridad

- El servidor escucha solo en `127.0.0.1`. En la Fase 2 el móvil entrará por un túnel o relay cifrado, no abriendo el puerto a la red.
- Al arrancar se crea el token del escritorio en `%APPDATA%\Nexora\desktop.token`; la app Tauri lo lee y lo envía. En la base de datos solo se guarda su SHA-256.
- `explain` solo lee rutas que ya están en el índice: el cliente envía un id, nunca una ruta.
- Las carpetas del feedback se validan (sin `..`, rutas absolutas ni nombres reservados de Windows) y quedan dentro de la raíz organizada, salvo que el usuario elija una carpeta propia (nunca la raíz del disco ni carpetas del sistema).
- Al proveedor de IA se le envían los nombres de las carpetas del usuario (`Documentos/Universidad`), no sus rutas absolutas.
- El contenido de los archivos se trata como dato en los prompts: el modelo tiene instrucciones de no obedecer órdenes que aparezcan en ellos.

## Proveedores de IA

`NEXORA_LLM_PROVEEDOR` = `anthropic` (por defecto, modelo `claude-opus-5-5`), `openai`, `gemini` u `ollama` (local; no envía datos fuera del PC). Con los tres últimos, `NEXORA_LLM_MODELO` es obligatorio.

Con `NEXORA_LLM_RESPALDO_PROVEEDOR` y `NEXORA_LLM_RESPALDO_MODELO`, al agotarse la cuota del principal se repite la operación con el respaldo y se vuelve a probar el principal con la siguiente petición cuando pasa el tiempo que indica el proveedor (15 minutos como máximo).

## Aprendizaje

- Cada archivo ubicado es un ejemplo en ChromaDB (`aprendizaje_ml`): de fuente `usuario` (confirmado o corregido) o `ia` (movido por el motor principal).
- El motor de respaldo (Ollama) ve ambos; los de fuente `ia` solo le dan sugerencias y no le autorizan a mover. El principal solo ve los del usuario. La decisión del usuario reemplaza a la de la IA.
- `POST /api/corrections` (`{"texto", "archivo_id"?}`) interpreta "X quedó mal, va en Y" y devuelve una propuesta; se aplica con `POST /api/feedback`, que guarda el texto en `motivo` para las siguientes clasificaciones.
- Embeddings: `paraphrase-multilingual-MiniLM-L12-v2` local (~135 MB, se descarga una vez en `<datos>/modelos`). Si no se puede descargar, se usa el básico de ChromaDB.
