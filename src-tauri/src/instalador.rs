//! Actualización de la app del teléfono: abre el instalador de Android con el APK que publica el PC.

use serde::{Deserialize, Serialize};
use tauri::{
    plugin::{Builder, TauriPlugin},
    Manager, Runtime,
};

#[cfg(target_os = "android")]
pub struct Instalador<R: Runtime>(tauri::plugin::PluginHandle<R>);

#[derive(Serialize)]
struct Args {
    ruta: String,
}

#[derive(Deserialize, Serialize)]
pub struct Resultado {
    /// false si Android tuvo que pedir antes el permiso de instalar apps de esta fuente.
    permiso: bool,
}

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("instalador")
        .setup(|_app, _api| {
            #[cfg(target_os = "android")]
            {
                let handle = _api.register_android_plugin("com.nexora.app", "InstaladorPlugin")?;
                _app.manage(Instalador(handle));
            }
            Ok(())
        })
        .build()
}

#[tauri::command]
pub async fn instalar_apk<R: Runtime>(app: tauri::AppHandle<R>, ruta: String) -> Result<Resultado, String> {
    #[cfg(target_os = "android")]
    {
        return app
            .state::<Instalador<R>>()
            .0
            .run_mobile_plugin::<Resultado>("instalar", Args { ruta })
            .map_err(|e| e.to_string());
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, Args { ruta });
        Err("La actualización automática solo está disponible en Android.".into())
    }
}
