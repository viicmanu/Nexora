use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent, State};

/// Proceso del motor de Python, solo si lo encendió esta ventana.
struct Motor(Mutex<Option<Child>>);

fn dir_datos() -> Result<PathBuf, String> {
    if let Some(dir) = std::env::var_os("NEXORA_DIR_DATOS") {
        return Ok(PathBuf::from(dir));
    }
    std::env::var_os("APPDATA")
        .map(|base| PathBuf::from(base).join("Nexora"))
        .ok_or_else(|| "No se encontró la carpeta de datos".to_string())
}

/// Token local que el motor crea en el perfil del usuario.
#[tauri::command]
fn token_escritorio() -> Result<String, String> {
    std::fs::read_to_string(dir_datos()?.join("desktop.token"))
        .map(|t| t.trim().to_string())
        .map_err(|e| e.to_string())
}

/// Enciende el motor de Python del proyecto si esta ventana aún no lo tiene en marcha.
/// Si ya hay otro motor en el puerto, la interfaz no llega a llamar a este comando.
#[tauri::command]
fn iniciar_motor(motor: State<Motor>) -> Result<(), String> {
    let mut actual = motor.0.lock().map_err(|e| e.to_string())?;
    if let Some(hijo) = actual.as_mut() {
        if matches!(hijo.try_wait(), Ok(None)) {
            return Ok(());
        }
    }
    let backend = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("backend");
    let python = backend.join(".venv").join("Scripts").join("python.exe");
    if !python.is_file() {
        return Err("No se encontró el entorno de Python en backend\\.venv".into());
    }
    let datos = dir_datos()?;
    std::fs::create_dir_all(&datos).map_err(|e| e.to_string())?;
    let registro = std::fs::File::create(datos.join("motor.log")).map_err(|e| e.to_string())?;
    let errores = registro.try_clone().map_err(|e| e.to_string())?;

    let mut comando = Command::new(python);
    comando
        .args(["-m", "nexora"])
        .current_dir(&backend)
        .stdin(Stdio::null())
        .stdout(registro)
        .stderr(errores);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        comando.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    *actual = Some(comando.spawn().map_err(|e| e.to_string())?);
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Motor(Mutex::new(None)))
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![token_escritorio, iniciar_motor])
        .build(tauri::generate_context!())
        .expect("error al iniciar Nexora")
        .run(|app, evento| {
            if let RunEvent::Exit = evento {
                if let Ok(mut motor) = app.state::<Motor>().0.lock() {
                    if let Some(mut hijo) = motor.take() {
                        let _ = hijo.kill();
                    }
                }
            }
        });
}
