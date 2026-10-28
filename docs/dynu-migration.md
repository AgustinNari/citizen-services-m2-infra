# Guía vigente: sslip.io → atencion-ciudadana.ddnsfree.com (Dynu)

Preparación local/GitHub del 27/10/2026. **No se ejecutó el cambio en Lightsail.**
No repetir la instalación HTTPS original. DuckDNS queda como investigación histórica
en [duckdns-migration.md](duckdns-migration.md); no usar sus comandos para Dynu.

## Dictamen y requisitos pendientes

La infraestructura ya es compatible: cambiar únicamente HTTPS_HOSTNAME y recrear
Caddy y Backend con las mismas imágenes. No cambiar Nginx, Frontend, PostgreSQL,
puertos, redes, datos ni volúmenes. Compose deriva M2_PUBLIC_BASE_URL del hostname;
Backend genera URLs HTTPS de adjuntos con esa base. Frontend usa /api y no depende
del proveedor DNS. El usuario deberá iniciar sesión en el nuevo origen; localStorage
del anterior no se transfiere. Los enlaces absolutos antiguos no se actualizan solos.

Se agregó un asistente porque reduce la repetición manual de edición, respaldos,
locks y comparación de estado. Reutiliza smoke-https.sh y el mismo lock/sentinel
que deploy-service.sh. No lo llama ningún workflow. No habilita/deshabilita CD ni
afirma verificar GitHub: exige la declaración del operador en los modos que mutan.

DNS público observado mediante Google y Cloudflare por HTTPS:

| Registro | Google | Cloudflare |
| --- | --- | --- |
| A de atencion-ciudadana.ddnsfree.com | 54.232.233.237, TTL 120 | 54.232.233.237, TTL 120 |
| AAAA del hostname | NOERROR, vacío | NOERROR, vacío |
| CAA del hostname | NOERROR, vacío | NOERROR, vacío |
| CAA de ddnsfree.com | NOERROR, vacío | NOERROR, vacío |
| CAA de com | NOERROR, vacío | NOERROR, vacío |

Esto elimina el bloqueo CAA observado en DuckDNS, pero **no garantiza emisión ni
renovación de certificados**. Repetir preflight en la ventana. Deben seguir abiertos
TCP 80/443 hacia Caddy; se requiere salida DNS/HTTPS, reloj correcto, espacio y los
volúmenes persistentes de Caddy escribibles. La comprobación por loopback no prueba
firewall, DNS del cliente ni aceptación desde Internet: se verifican por separado.

Fuentes oficiales consultadas:

- [Dynu FAQ](https://www.dynu.com/FAQ/Dynamic-DNS-Service) y
  [protocolo de actualización](https://www.dynu.com/DynamicDNS/IP-Update-Protocol):
  el cliente DDNS mantiene una IP cambiante. Aquí la IP es estática y el A ya está
  configurado: no hace falta cliente, cron, API ni token. Si cambia la IP, habrá que
  actualizar el registro conscientemente. La FAQ vincula propagación con TTL.
- [Condiciones de Dynu](https://www.dynu.com/en-US/Legal/TermsOfUse): no se encontró
  una obligación documentada de refrescar una IP estática cada N días. Mantener
  cuenta/contacto accesibles y revisar las condiciones; no prometer permanencia
  ilimitada ni disponibilidad garantizada del servicio gratuito.
- [Caddy: HTTPS automático](https://caddyserver.com/docs/automatic-https): la imagen
  oficial 2.11.7 sirve este hostname mediante los desafíos HTTP-01/TLS-ALPN-01.
  No se necesita módulo Dynu, DNS-01, wildcard ni TXT. Caddy mantiene emisión y
  renovación con Let's Encrypt y almacenamiento persistente.
- [Let's Encrypt: CAA](https://letsencrypt.org/docs/caa/),
  [IPv6](https://letsencrypt.org/docs/ipv6-support/) y
  [límites](https://letsencrypt.org/docs/rate-limits/): revisar también ascendentes
  CAA; no confundir NOERROR vacío con SERVFAIL. Una AAAA incorrecta puede desviar la
  validación. Respetar Retry-After, no reiniciar/empezar emisiones en bucle ni borrar
  /data. [ddnsfree.com figura en la PSL](https://publicsuffix.org/list/public_suffix_list.dat):
  no asumir un único límite de dominio compartido para todos los usuarios Dynu;
  siguen existiendo límites de cuenta, nombres y fallos de validación.
- [Filtrado por categorías](https://developers.cloudflare.com/cloudflare-one/traffic-controls/domain-categories/):
  DDNS y dominios nuevos pueden recibir políticas distintas por red. Dynu no evita
  necesariamente bloqueos de IP/TLS, VPN, cachés o filtros corporativos. Probar desde
  las redes reales; no desactivar protecciones ni validación TLS para dar por válido
  el cambio. No se contrató ningún servicio adicional.

## 1. GitHub: antes de copiar o aplicar

Solo en la futura ventana autorizada. En ambos repositorios de aplicación abrir
Settings → Secrets and variables → Actions → Variables y establecer CD_ENABLED=false.
Comprobar que ambos muestran false y esperar a que **todas** las ejecuciones activas
o pendientes terminen. Deshabilitar la variable no cancela jobs ya iniciados.

Alternativa en una terminal con GitHub CLI autenticada (no guardar credenciales aquí):

```bash
gh variable set CD_ENABLED --repo SantiMussi/front-desarrollo-apps-2 --body false
gh variable set CD_ENABLED --repo JuanmaGuida/Backend-DAMII --body false
gh variable get CD_ENABLED --repo SantiMussi/front-desarrollo-apps-2
gh variable get CD_ENABLED --repo JuanmaGuida/Backend-DAMII
for repo in SantiMussi/front-desarrollo-apps-2 JuanmaGuida/Backend-DAMII; do
  gh api --paginate "repos/$repo/actions/runs?per_page=100" \
    --jq '.workflow_runs[] | select(.status != "completed") | [.id,.name,.status,.html_url]'
done
```

Los get deben mostrar false y el bucle ninguna ejecución. No cancelar automáticamente
un deploy en curso. Mantener CD apagado hasta completar 5. Infra solo ejecuta CI.

## 2. PowerShell de Windows: paquete desde el commit validado

El push no sincroniza Lightsail. Usar el SHA completo del informe cuyo CI de Infra
terminó success. El empaquetador extrae archivos de ese commit con git archive;
rechaza archivos no versionados/symlinks. No incluye .env, images.env, Compose,
Caddyfile, datos, certificados, secretos ni binarios. Incluye tres scripts y esta
guía, COMMIT y SHA256SUMS; crea también un checksum del tar.

```powershell
Set-Location C:\dev\DA2\Infra\DA2-m2-infra
git fetch origin
$commit = Read-Host 'SHA completo del commit de preparación con CI correcto'
if ($commit -notmatch '^[0-9a-f]{40}$') { throw 'SHA inválido' }
gh run list --repo AgustinNari/citizen-services-m2-infra --commit $commit --json headSha,status,conclusion,url
# Confirmar success para ese SHA antes de continuar.
.\scripts\package-hostname-migration.ps1 -Commit $commit
$archive = Join-Path $PWD ".https-validation/hostname-package/hostname-$commit.tar"
scp -o StrictHostKeyChecking=yes $archive "$archive.sha256" ubuntu@54.232.233.237:/home/ubuntu/
```

Usar la identidad y known_hosts de la administración existente. No regenerar
known_hosts ni ignorar su comprobación. El empaquetador no sobrescribe un tar ya
existente: para repetir usar otro -OutputDirectory y ajustar $archive.

## 3. SSH Lightsail: instalar solo las herramientas verificadas

Abrir SSH con la identidad habitual, como ubuntu. No usar sudo para la migración
si el despliegue normal usa ubuntu: HOME debe coincidir con el lock de CD.
El ROOT debe ser el directorio desplegado; no crear otro proyecto o stack.

```bash
set -Eeuo pipefail
umask 077
ROOT=/home/ubuntu/DA2/Infra/DA2-m2-infra
cd "$ROOT"
read -rp 'El mismo SHA completo usado en PowerShell: ' COMMIT
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]]
ARCHIVE="$HOME/hostname-$COMMIT.tar"
(cd "$HOME" && sha256sum -c "hostname-$COMMIT.tar.sha256")
```

Instalar con este bloque completo. Comprueba lista exacta, hashes y SHA, rechaza
enlaces/rutas adicionales, toma el lock y guarda las versiones previas privadas.
No copia ninguna configuración runtime ni arranca servicios.

```bash
python3 - "$ARCHIVE" "$ROOT" "$COMMIT" <<'PY'
import fcntl, hashlib, os, pathlib, shutil, sys, tarfile, tempfile
archive, root, expected = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]).resolve(), sys.argv[3]
def check(condition, message):
    if not condition:
        raise SystemExit(message)
paths = {'scripts/migrate-https-hostname.sh', 'scripts/migrate_https_hostname.py',
         'scripts/smoke-https.sh', 'docs/dynu-migration.md'}
for name in ('compose/.env', 'compose/images.env', 'compose/compose.deploy.yml', 'caddy/Caddyfile'):
    check((root / name).is_file(), 'ROOT incorrecto o infraestructura no instalada')
with tarfile.open(archive) as source:
    members = source.getmembers()
    check(len(members) == len(paths) + 2, 'Cantidad inesperada de archivos')
    check({x.name for x in members} == paths | {'COMMIT', 'SHA256SUMS'}, 'Contenido inesperado')
    check(all(x.isfile() and x.size < 1024 * 1024 for x in members), 'Entrada no regular o demasiado grande')
    data = {x.name: source.extractfile(x).read() for x in members}
check(data['COMMIT'].decode().strip() == expected, 'Commit distinto')
manifest = dict(line.split('  ', 1)[::-1] for line in data['SHA256SUMS'].decode().splitlines())
check(set(manifest) == paths | {'COMMIT'}, 'Manifiesto incompleto')
check(all(hashlib.sha256(data[name]).hexdigest() == value for name, value in manifest.items()), 'Checksum incorrecto')
for name in paths:
    target = root / name
    check(not target.is_symlink() and target.parent.resolve().is_relative_to(root), 'Destino inesperado')
state = pathlib.Path.home() / 'DA2/.deploy'
state.mkdir(parents=True, exist_ok=True, mode=0o700)
with (state / 'm2-deploy.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    check(not (state / 'm2-maintenance').exists(), 'Ya hay mantenimiento; investigar')
    backup = pathlib.Path(tempfile.mkdtemp(prefix='hostname-tools-install-', dir=state))
    for name in sorted(paths):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            previous = backup / name
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, previous)
            previous.chmod(0o600)
        fd, temporary = tempfile.mkstemp(prefix='.hostname-install.', dir=target.parent)
        with os.fdopen(fd, 'wb') as output:
            output.write(data[name])
        os.chmod(temporary, 0o755 if name.startswith('scripts/') else 0o644)
        os.replace(temporary, target)
    (backup / 'COMMIT').write_text(expected + '\n')
print('Herramientas instaladas desde', expected, '; backup privado:', backup)
PY
```

Esperar el mensaje final sin errores. Ante error no aplicar; conservar tar y copias
para revisar. Este checksum presupone una transferencia SSH y un commit confiables;
no sustituye confiar en el origen del checksum. El script requiere Linux, Bash,
Python 3.10+, flock, Docker/Compose, curl y el almacén CA del sistema.

## 4. SSH: comprobar y aplicar (dos comandos)

```bash
bash scripts/migrate-https-hostname.sh preflight --hostname atencion-ciudadana.ddnsfree.com
```

Debe informar `Preflight passed` y el proyecto vigente, desde sslip.io al nuevo.
No recrea servicios: consulta DNS, Compose, contenedores/caché, HTTPS actual y
ejecuta Caddy validate en un contenedor efímero sin red ni datos TLS del servidor.
Puede crear el directorio/archivo de lock, sin modificar runtime. Comprueba que el
certificado actual conserve al menos un día de vigencia para facilitar rollback.
Si hay varios backends del mismo directorio, revisar y pasar --backend-container ID.

El control DNS exige A exacto, ninguna AAAA y CAA sin registros en ambos resolvers
y ascendentes. Si aparece un CAA legítimo, se detiene para revisión; no omitir el
control ni dar por compatible una política no interpretada por la herramienta.

Reconfirmar CD false y ninguna ejecución pendiente. Después autorizar exactamente
este hostname (estas opciones son una declaración del operador, no una consulta GitHub):

```bash
bash scripts/migrate-https-hostname.sh apply \
  --hostname atencion-ciudadana.ddnsfree.com \
  --confirm-hostname atencion-ciudadana.ddnsfree.com --cd-disabled-and-drained
```

El asistente repite preflight bajo el lock compartido, identifica el proyecto por
las etiquetas del backend en ROOT/compose y comprueba que el CD normal derivaría
ese mismo nombre. Rechaza un mantenimiento ajeno y referencias de imágenes que no
coincidan con los contenedores y la caché. Respalda archivos/estado con permisos
700/600 en ~/DA2/.deploy/hostname-..., crea el sentinel con su propietario y cambia
solo HTTPS_HOSTNAME, conservando modo y demás variables del .env.

Recrea una vez Caddy, espera TLS confiable por loopback con SNI y cadena/nombre
verificados por Python/OpenSSL, recrea una vez Backend y repite el smoke completo.
No hace pull ni toca Frontend/PostgreSQL. Compara imágenes, todos los montajes,
entorno y contenedores intactos. Configuración y respaldos que contengan secretos
no se imprimen. Cada comando externo tiene timeout; los reintentos de TLS/smoke
están limitados a doce, con pausas de tres segundos, sin recreaciones en bucle.

Copiar la ruta `Private backup:` que imprime. Se conserva también en
~/DA2/.deploy/hostname-last-backup. Ante error no sigue ni retira mantenimiento:
usar 6. El lock se libera al salir, incluso ante error; ningún hijo lo retiene.
No volver a ejecutar apply con mantenimiento activo: usar check, rollback o finish.
Si ya usa el hostname y no hay mantenimiento, apply solo comprueba y no recrea nada.

## 5. Verificar, aceptar y terminar

En SSH, recuperar la ruta impresa y verificar otra vez:

```bash
BACKUP=$(cat "$HOME/DA2/.deploy/hostname-last-backup")
bash scripts/migrate-https-hostname.sh check --backup "$BACKUP"
```

En **otra máquina/red**, sin --resolve y sin -k:

```bash
curl --noproxy '*' --fail --show-error --max-time 20 https://atencion-ciudadana.ddnsfree.com/
curl --noproxy '*' --fail --show-error --max-time 20 https://atencion-ciudadana.ddnsfree.com/api/catalog/categories
curl --noproxy '*' -sS --max-time 10 -D - -o /dev/null 'http://54.232.233.237/tickets?probe=cutover&n=1'
curl --noproxy '*' -sS --max-time 10 -D - -o /dev/null 'http://atencion-ciudadana.ddnsfree.com/tickets?probe=cutover&n=1'
```

Esperar HTML/JSON 200 y redirecciones 308 al nuevo HTTPS conservando ruta/query.
El smoke comprueba además /api → /api/, respuestas Actuator JSON UP y rechaza
redirecciones falsas, fallos TLS y SPA en lugar de JSON. No aceptar por `running`.

En navegador: certificado/SAN/fechas correctos, login nuevo, ruta React profunda,
ticket autorizado y descarga de un adjunto previo conservando nombre y bytes.
Comprobar en Network que las URLs de adjuntos devueltas usen el nuevo origen y no
haya mixed content ni saltos a sslip.io. Probar escritura solo con un ticket DEV
permitido. Repetir desde otra red disponible; DNS local correcto no prueba acceso
global. No copiar tokens a logs ni modificar datos reales para hacer un smoke.

Solo tras aceptar esas comprobaciones, en SSH:

```bash
bash scripts/migrate-https-hostname.sh finish --backup "$BACKUP" --accept --cd-disabled-and-drained
FRONTEND_IMAGE=$(awk -F= '$1=="FRONTEND_IMAGE" {sub(/\r$/, "", $2); print $2}' compose/images.env)
FRONTEND_SHA=${FRONTEND_IMAGE#ghcr.io/santimussi/front-desarrollo-apps-2:sha-}
[[ "$FRONTEND_SHA" =~ ^[0-9a-f]{40}$ ]]
bash scripts/deploy-service.sh frontend "$FRONTEND_SHA"
bash scripts/smoke-https.sh atencion-ciudadana.ddnsfree.com 54.232.233.237
```

finish vuelve a verificar todo y retira solo su marcador; el lock se libera antes
de deploy-service.sh, evitando tomarlo dos veces. El test normal usa el mismo SHA,
pero el script de CD consulta GHCR; un fallo del registro requiere diagnóstico y
mantener CD false, no revertir automáticamente un hostname sano. No borrar imágenes.

Reactivar CD solo con aceptación y autorización explícita: en GitHub volver ambos
CD_ENABLED a true y leerlos; no hay ningún comando del asistente que lo haga.
Observar el siguiente deploy autorizado de cada aplicación, sin commits vacíos.
Registrar backup, hostname, SHAs y fechas del certificado. Comprobar periódicamente
renovación/DNS/disco y conservar Caddy activo, sus volúmenes y TCP 80/443.

## 6. Reversión controlada a sslip.io

Mantener/reponer CD false y drenar ejecuciones como 1. En SSH, ROOT y BACKUP siguen
siendo los anteriores (si se perdió la sesión, cd al mismo ROOT y leer hostname-last-backup).

```bash
bash scripts/migrate-https-hostname.sh rollback --backup "$BACKUP" \
  --confirm-rollback --cd-disabled-and-drained
bash scripts/migrate-https-hostname.sh check --backup "$BACKUP"
```

Restaura exactamente el .env respaldado y recrea solo los servicios cuya actualización
se intentó, con imágenes locales y --pull never. Si falló antes de up, no agrega
reinicios. No toca PostgreSQL, adjuntos ni certificados; compara nuevamente estado
y smoke del hostname anterior. Si detecta cambios ajenos de archivos o imágenes,
se detiene antes de sobrescribirlos. Los respaldos de Compose/Caddy/images quedan
disponibles privados para recuperación supervisada, aunque esta migración no los edita.
Un rollback repetido ya verificado no vuelve a recrear servicios.

Tras un finish previo, rollback admite una nueva ventana confirmada y toma su propio
mantenimiento si no existe otro. Si hubo deployments o cambios posteriores, detenerse
y revisar: no usar una foto antigua para revertir trabajos nuevos. Un fallo durante
rollback conserva mantenimiento y evidencia; no declarar éxito ni reactivar CD.

Verificar externamente el HTTPS anterior, login y adjunto, y después ejecutar finish
con las mismas opciones de aceptación de 5. Mantener CD false hasta esa aceptación.
El certificado sslip.io permanece en el volumen; si caduca, su recuperación puede
necesitar emisión y DNS/CA disponibles, por eso se verifica su vigencia antes de aplicar.

No usar down, prune, Terraform apply ni borrar certificados, imágenes o volúmenes.
No retirar un sentinel ajeno manualmente. Si se interrumpió un proceso con Ctrl-C,
inspeccionar que no siga una operación Compose antes de iniciar la recuperación.

## Transición y límites de las pruebas

Al recrear Caddy, el sitio HTTPS antiguo deja de estar configurado; no hay redirección
HTTPS garantizada desde sslip.io. Un certificado en caché no conserva el sitio ni
garantiza su renovación futura. La IP HTTP redirige al nuevo. HSTS y cachés de 308
no transfieren usuarios entre hostnames: avisar la URL nueva y actualizar enlaces
externos. Si se requiere continuidad de ambos nombres, acordar ese alcance antes;
esta entrega no agrega dos sitios ni certificados adicionales.

Se reutilizan las pruebas de scripts y proxies, ampliadas para Dynu y un hostname
arbitrario. Se prueban localmente TLS con CA de fixture, rechazos de CA/nombre/ventana
de vigencia, DNS/Compose fallidos, locks reales, rollback, idempotencia y conservación
de estado. Las pruebas del asistente simulan Docker y no sustituyen el preflight
sobre el runtime real. No se probó emisión pública, firewall real, login desplegado
con el nuevo origen ni renovación: son los pendientes de la ventana autorizada.
