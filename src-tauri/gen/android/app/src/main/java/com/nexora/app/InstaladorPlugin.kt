package com.nexora.app

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import java.io.File

@InvokeArg
class ArgsInstalar {
  lateinit var ruta: String
}

/** Abre el instalador de Android con un APK descargado del PC (la actualización de Nexora). */
@TauriPlugin
class InstaladorPlugin(private val activity: Activity) : Plugin(activity) {
  @Command
  fun instalar(invoke: Invoke) {
    val args = invoke.parseArgs(ArgsInstalar::class.java)
    val apk = File(args.ruta)
    if (!apk.isFile) {
      invoke.reject("No se encontró la actualización descargada.")
      return
    }
    // Android pide permiso una vez para instalar apps que no vienen de la tienda.
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !activity.packageManager.canRequestPackageInstalls()) {
      activity.startActivity(
        Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}"))
      )
      val r = JSObject()
      r.put("permiso", false)
      invoke.resolve(r)
      return
    }
    val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.fileprovider", apk)
    val intent = Intent(Intent.ACTION_VIEW).apply {
      setDataAndType(uri, "application/vnd.android.package-archive")
      addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    activity.startActivity(intent)
    val r = JSObject()
    r.put("permiso", true)
    invoke.resolve(r)
  }
}
