//! Registro de fallos de la app del teléfono: panics de Rust y lectura de los informes guardados
//! (Android los guarda en files/fallos) para enviarlos al PC.

use std::{path::PathBuf, sync::OnceLock};

use tauri::{AppHandle, Manager, Runtime};

static CARPETA: OnceLock<PathBuf> = OnceLock::new();

fn carpetas<R: Runtime>(app: &AppHandle<R>) -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Ok(datos) = app.path().app_data_dir() {
        v.push(datos.join("fallos"));
        v.push(datos.join("files").join("fallos")); // en Android, la carpeta "files" de la app
    }
    if let Ok(datos) = app.path().app_local_data_dir() {
        v.push(datos.join("fallos"));
    }
    v.sort();
    v.dedup();
    v
}

/// Guarda un informe si Rust entra en pánico (antes de que la app se cierre).
pub fn instalar<R: Runtime>(app: &AppHandle<R>) {
    if let Some(carpeta) = carpetas(app).into_iter().next() {
        let _ = std::fs::create_dir_all(&carpeta);
        let _ = CARPETA.set(carpeta);
    }
    let anterior = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if let Some(carpeta) = CARPETA.get() {
            let momento = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis())
                .unwrap_or_default();
            let texto = format!(
                "tipo: panic_rust\nmomento_ms: {momento}\nhilo: {}\n\n{info}\n\n{}",
                std::thread::current().name().unwrap_or("?"),
                std::backtrace::Backtrace::force_capture()
            );
            let _ = std::fs::write(carpeta.join(format!("rust-{momento}.txt")), texto);
        }
        anterior(info);
    }));
}

#[derive(serde::Serialize)]
pub struct Informe {
    nombre: String,
    texto: String,
}

/// Informes de fallos pendientes de enviar al PC.
#[tauri::command]
pub fn leer_fallos<R: Runtime>(app: AppHandle<R>) -> Vec<Informe> {
    let mut informes = Vec::new();
    for carpeta in carpetas(&app) {
        let Ok(entradas) = std::fs::read_dir(&carpeta) else { continue };
        for e in entradas.flatten() {
            let nombre = e.file_name().to_string_lossy().into_owned();
            if let Ok(texto) = std::fs::read_to_string(e.path()) {
                informes.push(Informe { nombre, texto });
            }
        }
    }
    informes
}

/// Borra un informe ya enviado.
#[tauri::command]
pub fn borrar_fallo<R: Runtime>(app: AppHandle<R>, nombre: String) -> Result<(), String> {
    if nombre.contains(['/', '\\']) || nombre.contains("..") {
        return Err("Nombre no válido".into());
    }
    for carpeta in carpetas(&app) {
        let _ = std::fs::remove_file(carpeta.join(&nombre));
    }
    Ok(())
}
