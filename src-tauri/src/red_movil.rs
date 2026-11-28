//! Conexión del teléfono con el PC por la red local.
//!
//! Todo va por HTTPS con *certificate pinning*: no se confía en autoridades, solo en el certificado cuya huella
//! SHA-256 venía en el QR. Se compila también en escritorio para poder probar la app móvil en el PC.

use std::{
    io::{Read, Seek, SeekFrom},
    sync::Arc,
    time::Duration,
};

use futures_util::StreamExt;
use rustls::{
    client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier},
    pki_types::{CertificateDer, ServerName, UnixTime},
    DigitallySignedStruct, SignatureScheme,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use tauri::{ipc::InvokeBody, AppHandle, Emitter, Manager};
use tokio::io::AsyncWriteExt;

#[derive(Debug)]
struct Huella {
    esperada: Vec<u8>,
    proveedor: Arc<rustls::crypto::CryptoProvider>,
}

impl ServerCertVerifier for Huella {
    fn verify_server_cert(
        &self,
        certificado: &CertificateDer<'_>,
        _intermedios: &[CertificateDer<'_>],
        _nombre: &ServerName<'_>,
        _ocsp: &[u8],
        _ahora: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        if Sha256::digest(certificado.as_ref()).as_slice() == self.esperada.as_slice() {
            Ok(ServerCertVerified::assertion())
        } else {
            Err(rustls::Error::General("El certificado no es el del PC vinculado".into()))
        }
    }

    fn verify_tls12_signature(&self, m: &[u8], c: &CertificateDer<'_>, d: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(m, c, d, &self.proveedor.signature_verification_algorithms)
    }

    fn verify_tls13_signature(&self, m: &[u8], c: &CertificateDer<'_>, d: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(m, c, d, &self.proveedor.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.proveedor.signature_verification_algorithms.supported_schemes()
    }
}

fn cliente(huella: &str, espera: Duration) -> Result<reqwest::Client, String> {
    let esperada = hex::decode(huella).map_err(|_| "Huella del PC no válida".to_string())?;
    let proveedor = Arc::new(rustls::crypto::ring::default_provider());
    let config = rustls::ClientConfig::builder_with_provider(proveedor.clone())
        .with_safe_default_protocol_versions()
        .map_err(|e| e.to_string())?
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(Huella { esperada, proveedor }))
        .with_no_client_auth();
    reqwest::Client::builder()
        .use_preconfigured_tls(config)
        .connect_timeout(Duration::from_secs(4))
        .timeout(espera)
        .build()
        .map_err(|e| e.to_string())
}

#[derive(Serialize)]
pub struct Respuesta {
    estado: u16,
    cuerpo: String,
}

fn error_red(e: reqwest::Error) -> String {
    // "sin_conexion:" permite a la interfaz distinguir "PC desconectado" de un error de la API.
    format!("sin_conexion: {e}")
}

async fn responder(r: reqwest::Response) -> Result<Respuesta, String> {
    let estado = r.status().as_u16();
    Ok(Respuesta { estado, cuerpo: r.text().await.map_err(error_red)? })
}

/// Petición JSON al PC vinculado.
#[tauri::command]
pub async fn red_peticion(
    url: String,
    metodo: String,
    huella: String,
    token: Option<String>,
    cuerpo: Option<String>,
) -> Result<Respuesta, String> {
    let c = cliente(&huella, Duration::from_secs(30))?;
    let metodo = reqwest::Method::from_bytes(metodo.as_bytes()).map_err(|e| e.to_string())?;
    let mut p = c.request(metodo, &url);
    if let Some(t) = token {
        p = p.bearer_auth(t);
    }
    if let Some(cuerpo) = cuerpo {
        p = p.header("Content-Type", "application/json").body(cuerpo);
    }
    responder(p.send().await.map_err(error_red)?).await
}

/// Envía un fragmento de un archivo local, leyendo solo esa parte del disco.
#[tauri::command]
pub async fn red_subir_fragmento(
    url: String,
    huella: String,
    token: String,
    ruta: String,
    desde: u64,
    longitud: u64,
) -> Result<Respuesta, String> {
    let datos = tokio::task::spawn_blocking(move || -> Result<Vec<u8>, String> {
        let mut f = std::fs::File::open(&ruta).map_err(|e| e.to_string())?;
        f.seek(SeekFrom::Start(desde)).map_err(|e| e.to_string())?;
        let mut datos = vec![0; longitud as usize];
        f.read_exact(&mut datos).map_err(|e| e.to_string())?;
        Ok(datos)
    })
    .await
    .map_err(|e| e.to_string())??;
    let c = cliente(&huella, Duration::from_secs(300))?;
    let r = c
        .put(format!("{url}?offset={desde}"))
        .bearer_auth(token)
        .header("Content-Type", "application/octet-stream")
        .body(datos)
        .send()
        .await
        .map_err(error_red)?;
    responder(r).await
}

#[derive(Clone, Serialize)]
struct Progreso {
    id: String,
    recibidos: u64,
    total: Option<u64>,
}

/// Descarga por streaming a `destino`, avisando del progreso con el evento "descarga".
#[tauri::command]
pub async fn red_descargar(app: AppHandle, url: String, huella: String, token: String, destino: String, id: String) -> Result<u64, String> {
    let c = cliente(&huella, Duration::from_secs(3600))?;
    let r = c.get(&url).bearer_auth(token).send().await.map_err(error_red)?;
    if !r.status().is_success() {
        return Err(format!("El PC respondió {}", r.status()));
    }
    let total = r.content_length();
    let parcial = format!("{destino}.part");
    let mut archivo = tokio::fs::File::create(&parcial).await.map_err(|e| e.to_string())?;
    let mut recibidos = 0u64;
    let mut flujo = r.bytes_stream();
    while let Some(bloque) = flujo.next().await {
        let bloque = bloque.map_err(error_red)?;
        archivo.write_all(&bloque).await.map_err(|e| e.to_string())?;
        recibidos += bloque.len() as u64;
        let _ = app.emit("descarga", Progreso { id: id.clone(), recibidos, total });
    }
    archivo.flush().await.map_err(|e| e.to_string())?;
    drop(archivo);
    tokio::fs::rename(&parcial, &destino).await.map_err(|e| e.to_string())?;
    Ok(recibidos)
}

/// SHA-256 y tamaño de un archivo local (para verificar la subida en el PC).
#[tauri::command]
pub async fn archivo_sha256(ruta: String) -> Result<(String, u64), String> {
    tokio::task::spawn_blocking(move || {
        let mut f = std::fs::File::open(&ruta).map_err(|e| e.to_string())?;
        let mut h = Sha256::new();
        let mut bloque = vec![0u8; 1 << 20];
        let mut total = 0u64;
        loop {
            let n = f.read(&mut bloque).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            h.update(&bloque[..n]);
            total += n as u64;
        }
        Ok((hex::encode(h.finalize()), total))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Busca el PC vinculado en la Wi-Fi por mDNS (_nexora._tcp) y devuelve sus IP.
#[tauri::command]
pub async fn buscar_pc(pc_id: String, segundos: u64) -> Result<Vec<String>, String> {
    tokio::task::spawn_blocking(move || {
        let demonio = mdns_sd::ServiceDaemon::new().map_err(|e| e.to_string())?;
        let receptor = demonio.browse("_nexora._tcp.local.").map_err(|e| e.to_string())?;
        let limite = std::time::Instant::now() + Duration::from_secs(segundos.clamp(1, 15));
        let mut ips = Vec::new();
        while let Some(restante) = limite.checked_duration_since(std::time::Instant::now()) {
            match receptor.recv_timeout(restante) {
                Ok(mdns_sd::ServiceEvent::ServiceResolved(info)) => {
                    if info.get_property_val_str("id") == Some(pc_id.as_str()) {
                        ips.extend(info.get_addresses().iter().filter(|ip| ip.is_ipv4()).map(|ip| ip.to_string()));
                        break;
                    }
                }
                Ok(_) => {}
                Err(_) => break,
            }
        }
        let _ = demonio.shutdown();
        Ok(ips)
    })
    .await
    .map_err(|e| e.to_string())?
}

fn ruta_cola(app: &AppHandle, id: &str) -> Result<std::path::PathBuf, String> {
    if id.is_empty() || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("Identificador no válido".into());
    }
    let carpeta = app.path().app_data_dir().map_err(|e| e.to_string())?.join("cola");
    std::fs::create_dir_all(&carpeta).map_err(|e| e.to_string())?;
    Ok(carpeta.join(id))
}

/// Copia un archivo elegido a la cola de la app (rutas normales; en Android las URI content:// usan `escribir_cola`).
#[tauri::command]
pub async fn copiar_a_cola(app: AppHandle, origen: String, id: String) -> Result<String, String> {
    let destino = ruta_cola(&app, &id)?;
    tokio::fs::copy(&origen, &destino).await.map_err(|e| e.to_string())?;
    Ok(destino.to_string_lossy().into_owned())
}

/// Guarda en la cola bytes enviados en binario (fotos de la cámara, URI de Android). El id va en la cabecera x-id.
#[tauri::command]
pub async fn escribir_cola(app: AppHandle, request: tauri::ipc::Request<'_>) -> Result<String, String> {
    let InvokeBody::Raw(datos) = request.body() else {
        return Err("Se esperaban bytes".into());
    };
    let id = request.headers().get("x-id").and_then(|v| v.to_str().ok()).unwrap_or_default();
    let destino = ruta_cola(&app, id)?;
    tokio::fs::write(&destino, datos).await.map_err(|e| e.to_string())?;
    Ok(destino.to_string_lossy().into_owned())
}
