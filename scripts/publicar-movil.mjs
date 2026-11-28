// Compila la app del teléfono (APK release firmada) y la publica para que los teléfonos vinculados se actualicen solos.
//
//   pnpm movil:publicar
//
// Necesita JAVA_HOME (JDK 17-21), ANDROID_HOME y NDK_HOME. La firma se crea la primera vez en
// %APPDATA%/Nexora/firma (guárdala: sin ella, los teléfonos no aceptan las siguientes actualizaciones).
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const raiz = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const datos = process.env.NEXORA_DIR_DATOS ?? join(process.env.APPDATA, 'Nexora')
const firma = join(datos, 'firma')
const almacen = join(firma, 'nexora.jks')
const propiedades = join(raiz, 'src-tauri', 'gen', 'android', 'keystore.properties')

// 1. Firma propia (una vez). La contraseña solo vive en el perfil del usuario y en un archivo ignorado por Git.
if (!existsSync(almacen)) {
  mkdirSync(firma, { recursive: true })
  const clave = randomBytes(18).toString('base64url')
  const keytool = join(process.env.JAVA_HOME ?? '', 'bin', 'keytool')
  execFileSync(keytool, ['-genkeypair', '-keystore', almacen, '-alias', 'nexora', '-keyalg', 'RSA', '-keysize', '2048',
    '-validity', '10000', '-storepass', clave, '-keypass', clave, '-dname', 'CN=Nexora'], { stdio: 'inherit' })
  writeFileSync(join(firma, 'clave.txt'), clave)
  console.log(`Firma creada en ${firma}. Haz una copia de seguridad de esa carpeta.`)
}
const clave = readFileSync(join(firma, 'clave.txt'), 'utf8').trim()
writeFileSync(propiedades, `storeFile=${almacen.replaceAll('\\', '/')}\nstorePassword=${clave}\nkeyAlias=nexora\nkeyPassword=${clave}\n`)

// 2. Número de versión creciente (minutos desde 2026): Android y el teléfono lo comparan para saber si hay algo nuevo.
const codigo = Math.floor((Date.now() - Date.UTC(2026, 0, 1)) / 60000)
// Por archivo: en Windows la consola quita las comillas de un JSON pasado como texto.
const config = join(tmpdir(), `nexora-version-${codigo}.json`)
writeFileSync(config, JSON.stringify({ bundle: { android: { versionCode: codigo } } }))
const r = spawnSync('pnpm', ['tauri', 'android', 'build', '--target', 'aarch64', '--apk', '--config', config], {
  cwd: raiz, stdio: 'inherit', shell: true, env: { ...process.env, VITE_VERSION_MOVIL: String(codigo) },
})
if (r.status !== 0) process.exit(r.status ?? 1)

// 3. Publicar: el motor del PC lo ofrece a los teléfonos en /api/devices/app y /api/devices/app/apk.
const apk = join(raiz, 'src-tauri', 'gen', 'android', 'app', 'build', 'outputs', 'apk', 'universal', 'release', 'app-universal-release.apk')
const destino = join(datos, 'app_movil')
mkdirSync(destino, { recursive: true })
copyFileSync(apk, join(destino, 'nexora.apk'))
const bytes = readFileSync(apk)
writeFileSync(join(destino, 'version.json'), JSON.stringify({
  codigo, fecha: new Date().toISOString(), tamano: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
}, null, 2))
copyFileSync(apk, join(process.env.USERPROFILE, 'Desktop', 'Nexora.apk'))
console.log(`Publicada la versión ${codigo} (${(bytes.length / 1048576).toFixed(1)} MB). Copia en el Escritorio: Nexora.apk`)
