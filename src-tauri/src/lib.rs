/// Comando de ejemplo invocable desde React con `invoke('greet', { name })`.
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hola, {name}. Nexora está lista.")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
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
        .invoke_handler(tauri::generate_handler![greet])
        .run(tauri::generate_context!())
        .expect("error al iniciar Nexora");
}
