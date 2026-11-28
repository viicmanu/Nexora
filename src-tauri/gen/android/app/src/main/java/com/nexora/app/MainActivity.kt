package com.nexora.app

import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.content.Context
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : TauriActivity() {
  // Sin este bloqueo, Android descarta los paquetes multicast y el teléfono no encuentra el PC por mDNS.
  private var multicast: WifiManager.MulticastLock? = null

  override fun onCreate(savedInstanceState: Bundle?) {
    capturarFallos()
    super.onCreate(savedInstanceState)
    val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    multicast = wifi.createMulticastLock("nexora-mdns").apply {
      setReferenceCounted(false)
      acquire()
    }
  }

  override fun onDestroy() {
    multicast?.release()
    super.onDestroy()
  }

  // --- Registro de fallos: se guardan en files/fallos y la app los envía al PC al reconectar ---

  private fun carpetaFallos() = File(filesDir, "fallos").apply { mkdirs() }

  private fun informe(tipo: String, detalle: String): String {
    val fecha = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ", Locale.ROOT).format(Date())
    return "tipo: $tipo\nfecha: $fecha\ndispositivo: ${Build.MANUFACTURER} ${Build.MODEL}\n" +
      "android: ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})\nversion_app: ${BuildConfig.VERSION_CODE}\n\n$detalle"
  }

  private fun guardar(nombre: String, texto: String) {
    try { File(carpetaFallos(), nombre).writeText(texto.take(300_000)) } catch (_: Throwable) {}
  }

  private fun capturarFallos() {
    // 1. Excepciones de Java/Kotlin: se escribe el informe y se deja que Android cierre la app como siempre.
    val anterior = Thread.getDefaultUncaughtExceptionHandler()
    Thread.setDefaultUncaughtExceptionHandler { hilo, error ->
      guardar("java-${System.currentTimeMillis()}.txt", informe("excepcion_java", "hilo: ${hilo.name}\n\n${Log.getStackTraceString(error)}"))
      anterior?.uncaughtException(hilo, error)
    }
    // 2. Cierres que la app no pudo registrar (fallos nativos, app que no responde, falta de memoria): Android los
    //    recuerda y se recuperan al volver a abrirla.
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
      try {
        val prefs = getSharedPreferences("fallos", Context.MODE_PRIVATE)
        val revisadoHasta = prefs.getLong("revisado_hasta", 0)
        val am = getSystemService(ActivityManager::class.java)
        val motivos = mapOf(
          ApplicationExitInfo.REASON_CRASH to "cierre_java",
          ApplicationExitInfo.REASON_CRASH_NATIVE to "cierre_nativo",
          ApplicationExitInfo.REASON_ANR to "app_no_responde",
          ApplicationExitInfo.REASON_LOW_MEMORY to "falta_de_memoria",
        )
        for (salida in am.getHistoricalProcessExitReasons(packageName, 0, 10)) {
          val tipo = motivos[salida.reason] ?: continue
          if (salida.timestamp <= revisadoHasta) continue
          // La traza de un ANR es texto; la de un fallo nativo es binaria y no se incluye.
          val traza = if (salida.reason == ApplicationExitInfo.REASON_ANR)
            salida.traceInputStream?.bufferedReader()?.use { it.readText() } ?: "" else ""
          guardar("sistema-${salida.timestamp}.txt", informe(tipo,
            "descripcion: ${salida.description}\nimportancia: ${salida.importance}\npss_kb: ${salida.pss}\nrss_kb: ${salida.rss}\n" +
              "momento: ${Date(salida.timestamp)}\n\n$traza"))
        }
        prefs.edit().putLong("revisado_hasta", System.currentTimeMillis()).apply()
      } catch (_: Throwable) {}
    }
  }
}
