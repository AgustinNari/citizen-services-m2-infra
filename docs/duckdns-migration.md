# Migración manual de sslip.io a DuckDNS

Preparación del 27/10/2026. **No ejecutar el cambio mientras el control DNS de A
falle.** Esta entrega no cambia Lightsail, DNS, certificados ni variables GitHub.
La aplicación sigue usando `54-232-233-237.sslip.io` hasta la intervención manual.

## Dictamen y alcance

La configuración existente es compatible: basta cambiar **solo** HTTPS_HOSTNAME
en el `.env` privado y recrear Caddy y Backend con las mismas imágenes. Caddy usa
esa variable para TLS, Host, cabeceras y redirecciones; Compose deriva
`M2_PUBLIC_BASE_URL=https://${HTTPS_HOSTNAME}`. No hay que reconstruir Frontend,
cambiar Nginx, imágenes, IP, puertos, PostgreSQL ni volúmenes.

Frontend usa `/api` y tokens en localStorage; el login guardado pertenece al origen
antiguo y habrá que iniciar sesión en el nuevo. Backend es stateless y genera las
URLs de adjuntos al responder usando la base configurada; no se migran archivos ni
registros. No se encontró dependencia funcional de sslip.io en esas fuentes.
Enlaces absolutos ya copiados o guardados fuera de la aplicación seguirán apuntando
al hostname anterior y deberán actualizarse. Integraciones externas futuras que
usen callbacks/orígenes también deberán revisar su configuración.

**READY_FOR_MANUAL_DNS_CUTOVER: NO**, por el resultado CAA descrito a continuación.
La preparación de código y procedimiento está lista; no se probó una emisión real.

## Investigación y DNS observado

Consultas públicas del 27/10/2026, sin contactar la IP de Lightsail:

| Consulta | Google DNS por HTTPS | Cloudflare DNS por HTTPS |
| --- | --- | --- |
| A del nuevo hostname | 54.232.233.237, TTL 60 | 54.232.233.237, TTL 60 |
| AAAA del nuevo hostname | NOERROR, sin respuesta AAAA | NOERROR, sin respuesta AAAA |
| CAA del nuevo hostname | **SERVFAIL**, reiterado | NOERROR, sin CAA |
| CAA de duckdns.org y org | NOERROR, sin CAA | NOERROR, sin CAA |

Google informa `Unexpected .../a ... for .../caa`. Se consultaron también tres
servidores autoritativos de DuckDNS por UDP 53 (15.222.19.97, 99.79.143.35 y
3.97.58.28): devolvieron NOERROR pero un registro **A, tipo 1**, ante la consulta
**CAA, tipo 257**. Esto respalda el diagnóstico de respuesta DNS incompatible;
no demuestra que todas las redes o resolvers fallen igual. No se intentó obtener
un certificado para comprobar el comportamiento particular de Let's Encrypt.

Un A correcto no demuestra que TLS esté disponible. La CA debe poder consultar CAA
y validar el dominio desde su propia red. **No iniciar B/C hasta que CAA sea
consistente y válido.** Si persiste, comunicar la evidencia al soporte de DuckDNS
o evaluar otro proveedor en una tarea separada. Cambiar el resolver local de Caddy
no corrige los resolvers usados por la CA; no se recomienda como solución.

Fuentes oficiales consultadas:

- [DuckDNS: especificación](https://www.duckdns.org/spec.jsp) y
  [funcionamiento](https://www.duckdns.org/why.jsp): servicio gratuito que mantiene
  registros IPv4/IPv6 y permite TXT. Los clientes periódicos sirven para actualizar
  una IP cambiante. Con la IP estática ya registrada no hace falta instalar cliente,
  cron ni usar un token; tampoco se necesita DNS-01 para este hostname individual.
- [FAQ](https://www.duckdns.org/faqs.jsp) y
  [condiciones](https://www.duckdns.org/tac.jsp): no se encontró una obligación
  publicada de actualizar cada cierto número de días para conservar el dominio.
  Esto no constituye garantía de permanencia: las condiciones permiten suspensión
  y no garantizan servicio ininterrumpido. Mantener acceso a la cuenta, uso lícito
  y revisar periódicamente las condiciones; no inventar un plazo de caducidad ni
  agregar actualizaciones periódicas como supuesto requisito.
- [HTTPS automático de Caddy](https://caddyserver.com/docs/automatic-https) y
  [Caddy 2.11.7](https://github.com/caddyserver/caddy/releases/tag/v2.11.7): un nombre
  DNS público funciona con la imagen oficial y los desafíos HTTP-01/TLS-ALPN-01.
  No se necesita plugin DuckDNS, DNS API, wildcard ni certificado manual. Mantener
  80/443 TCP accesibles, salida DNS/HTTPS, reloj correcto y almacenamiento de Caddy
  persistente/escribible. El issuer del proyecto sigue siendo Let's Encrypt.
- [CAA de Let's Encrypt](https://letsencrypt.org/docs/caa/): ausencia válida de CAA
  no equivale a SERVFAIL. Las restricciones del ancestro más cercano también
  importan; si aparecen registros, deben permitir `letsencrypt.org` y los desafíos
  utilizados. Un error DNS puede impedir emisión aun sin CAA configurado.
- [IPv6](https://letsencrypt.org/docs/ipv6-support/): una AAAA incorrecta puede
  enviar la validación a otro servidor. Aquí debe permanecer ausente mientras no
  haya una IPv6 configurada y accesible de extremo a extremo.
- [Límites](https://letsencrypt.org/docs/rate-limits/) y
  [Public Suffix List](https://publicsuffix.org/list/public_suffix_list.dat):
  DuckDNS figura como sufijo público; no corresponde asumir que todos sus usuarios
  comparten un único límite por dominio registrado. Siguen aplicando límites de
  cuenta, identificador, conjunto de nombres y validaciones fallidas. Respetar
  Retry-After; no reiniciar/empezar emisiones en bucle ni borrar almacenamiento.
  Caddy renueva automáticamente; comprobar fechas y errores durante la operación.
- [Filtrado DNS por categorías](https://developers.cloudflare.com/cloudflare-one/traffic-controls/domain-categories/):
  cambiar de proveedor puede evitar un bloqueo específico de sslip.io, pero no
  garantiza acceso en redes que bloquean DDNS, dominios nuevos, la IP o el tráfico
  TLS. Cachés, VPN, DNS seguro del navegador y políticas del router pueden diferir.
  Como inferencia técnica, una protección contra rebinding que rechaza respuestas
  privadas no debería rechazar por ese motivo la IPv4 pública 54.232.233.237;
  no se diagnosticó el bloqueo concreto de ninguna red del usuario. No desactivar
  protecciones globales: revisar el error y, si corresponde, una excepción aprobada.

## A. Preparación: terminal administrativa, antes de entrar por SSH

Estas son instrucciones **para la futura intervención autorizada**. No deben
ejecutarse durante la preparación local/GitHub. Requieren curl/Python 3 y, para
los comandos GitHub, GitHub CLI autenticada con permisos de variables/Actions.

En una terminal Bash administrativa definir:

```bash
NEW_HOST=atencion-ciudadana.duckdns.org
OLD_HOST=54-232-233-237.sslip.io
LEGACY_IP=54.232.233.237
```

Ejecutar este control DNS de solo lectura. Debe terminar con `DNS preflight passed`.
También se ejecutará desde la terminal SSH antes de cambiar configuración.
Con el incidente observado devuelve error y **la migración se detiene aquí**.

```bash
python3 - "$NEW_HOST" "$LEGACY_IP" <<'PY'
import json, sys, urllib.parse, urllib.request
host, ip = sys.argv[1:]
for base in ('https://dns.google/resolve', 'https://cloudflare-dns.com/dns-query'):
    checks = [(host, 'A'), (host, 'AAAA')]
    checks += [(name, 'CAA') for name in (host, 'duckdns.org', 'org')]
    for name, kind in checks:
        url = base + '?' + urllib.parse.urlencode({'name': name, 'type': kind})
        request = urllib.request.Request(url, headers={'Accept': 'application/dns-json'})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        assert data['Status'] == 0, (base, name, kind, data)
        answers = data.get('Answer', [])
        if kind == 'A':
            assert answers and all(x['type'] == 1 and x['data'] == ip for x in answers), answers
        elif kind == 'AAAA':
            assert not answers, ('Investigar AAAA inesperada', answers)
        else:
            assert not answers, ('Revisar CAA con el operador antes de continuar', answers)
        print(base, name, kind, 'OK')
print('DNS preflight passed')
PY
```

Este control espera la configuración actual sin CAA. Si en el futuro aparece un
CAA legítimo, revisarlo con las reglas de la CA antes de adaptar el control; no
ignorar errores ni deshabilitar TLS. Un timeout de DoH tampoco permite continuar.

En la terminal administrativa, deshabilitar CD de ambos repositorios y leer el
valor aplicado. Alternativa en la web: Settings → Secrets and variables → Actions
→ Variables → CD_ENABLED. Esto **no** cambia el servidor ni cancela jobs ya iniciados.

```bash
gh variable set CD_ENABLED --repo SantiMussi/front-desarrollo-apps-2 --body false
gh variable set CD_ENABLED --repo JuanmaGuida/Backend-DAMII --body false
gh variable get CD_ENABLED --repo SantiMussi/front-desarrollo-apps-2
gh variable get CD_ENABLED --repo JuanmaGuida/Backend-DAMII
```

Ambos deben imprimir `false`. Esperar a que no quede ninguna ejecución incompleta;
no cancelar ni ignorar automáticamente un deploy que ya haya empezado.

```bash
for repo in SantiMussi/front-desarrollo-apps-2 JuanmaGuida/Backend-DAMII; do
  gh api --paginate "repos/$repo/actions/runs?per_page=100" \
    --jq '.workflow_runs[] | select(.status != "completed") | [.id,.name,.status,.html_url]'
done
```

Resultado esperado: ninguna línea. Confirmar también en Actions antes de B.
En Lightsail Networking comprobar manualmente que TCP 80/443 siguen abiertos;
no añadir puertos internos ni cambiar reglas que ya permiten el HTTPS actual.
No se necesita Terraform apply. Desde otra red, confirmar que el HTTPS antiguo
actual responde antes de programar la ventana. No probar emisión del nuevo aún.

## B. Seguridad y respaldo: terminal SSH de Lightsail

Solo después de resolver A y aprobar la ventana, conectarse con la identidad SSH
administrativa existente. Mantener la misma sesión Bash hasta E. Cada fallo obliga
a detenerse; si ya se intentó recrear un servicio, usar F. No cerrar la sesión
dejando una migración parcial sin revisar su estado.

```bash
set -Eeuo pipefail
umask 077
ROOT=/home/ubuntu/DA2/Infra/DA2-m2-infra
NEW_HOST=atencion-ciudadana.duckdns.org
OLD_HOST=54-232-233-237.sslip.io
LEGACY_IP=54.232.233.237
cd "$ROOT"
test "$HOME" = /home/ubuntu
```

Repetir aquí el bloque DNS de A. Comprobar los listeners existentes (deben seguir
siendo Caddy/Docker para 80/443 y Backend solo loopback 8080):

```bash
sudo ss -ltnp '( sport = :80 or sport = :443 or sport = :8080 )'
docker ps --filter label=com.docker.compose.service=backend --format 'table {{.ID}}\t{{.Names}}'
read -rp 'ID del Backend M2 actualmente desplegado: ' BACKEND_CONTAINER
COMPOSE_PROJECT_NAME=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$BACKEND_CONTAINER")
WORKING_DIR=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$BACKEND_CONTAINER")
test -n "$COMPOSE_PROJECT_NAME"
test "$WORKING_DIR" = "$ROOT/compose"
export COMPOSE_PROJECT_NAME
```

No escoger otro proyecto por conveniencia. Definir Compose con los archivos privados
existentes, nunca con ejemplos. No imprimir `dc config` completo: contiene secretos.

```bash
dc() {
  docker compose --project-directory "$ROOT/compose" \
    --env-file "$ROOT/compose/.env" --env-file "$ROOT/compose/images.env" \
    -f "$ROOT/compose/compose.deploy.yml" "$@"
}
dc config --quiet
normal_project=$(env -u COMPOSE_PROJECT_NAME docker compose --project-directory "$ROOT/compose" \
  --env-file "$ROOT/compose/.env" --env-file "$ROOT/compose/images.env" \
  -f "$ROOT/compose/compose.deploy.yml" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
test "$normal_project" = "$COMPOSE_PROJECT_NAME"
```

La última comprobación asegura que el script normal de CD usará el mismo proyecto
sin depender de esta sesión. Si falla, investigar y resolver esa configuración
por separado; no continuar creando otro proyecto ni cambiar más variables aquí.

Tomar el mismo lock que usa deploy-service.sh y crear el sentinel solo si no existía:

```bash
DEPLOY_STATE="$HOME/DA2/.deploy"
mkdir -p "$DEPLOY_STATE"
chmod 700 "$DEPLOY_STATE"
exec 9>"$DEPLOY_STATE/m2-deploy.lock"
flock -w 600 9
test ! -e "$DEPLOY_STATE/m2-maintenance"
touch "$DEPLOY_STATE/m2-maintenance"
BACKUP="$DEPLOY_STATE/duckdns-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$BACKUP"
printf '%s\n' "$BACKUP" > "$DEPLOY_STATE/duckdns-backup-path"
```

Si había mantenimiento de otra operación, detenerse y coordinar; no retirarlo.
Guardar configuración y estado; los archivos de backup quedan privados fuera del repo:

```bash
cp -p compose/.env "$BACKUP/runtime.env.private"
cp -p compose/images.env "$BACKUP/images.env"
cp -p compose/compose.deploy.yml "$BACKUP/compose.deploy.yml"
cp -p caddy/Caddyfile "$BACKUP/Caddyfile"
printf '%s\n' "$COMPOSE_PROJECT_NAME" > "$BACKUP/project-name"
dc ps -q postgres > "$BACKUP/postgres.id"
dc ps -q frontend > "$BACKUP/frontend.id"
test -s "$BACKUP/postgres.id"
test -s "$BACKUP/frontend.id"
docker inspect --format '{{json .Config.Env}}' "$(dc ps -q backend)" > "$BACKUP/backend-env.private.json"
```

Registrar montajes e imágenes y comprobar que los tags resuelven a las imágenes
ya ejecutadas. No se hará pull; si falta una imagen o difiere, detenerse antes de C.

```bash
snapshot_mounts() {
  for service in postgres backend caddy; do
    docker inspect --format '{{range .Mounts}}{{println .Type .Name .Source .Destination}}{{end}}' "$(dc ps -q "$service")"
  done
}
snapshot_mounts > "$BACKUP/mounts.before"
for service in caddy backend frontend; do
  container=$(dc ps -q "$service")
  image_ref=$(docker inspect --format '{{.Config.Image}}' "$container")
  image_id=$(docker inspect --format '{{.Image}}' "$container")
  test "$(docker image inspect --format '{{.Id}}' "$image_ref")" = "$image_id"
  printf '%s\n' "$image_id" > "$BACKUP/$service.image-id"
done
bash scripts/smoke-https.sh "$OLD_HOST" "$LEGACY_IP"
```

El smoke antiguo debe pasar, incluido Actuator v3+json (corrección f63ca81 o
equivalente). Si falla, no continuar. Comprobar además que el certificado de rollback
no esté por vencer dentro de la ventana (este control exige al menos un día):

```bash
openssl s_client -connect 127.0.0.1:443 -servername "$OLD_HOST" \
  -verify_hostname "$OLD_HOST" -verify_return_error </dev/null \
  2>"$BACKUP/old-tls-verification.txt" | openssl x509 -noout -checkend 86400
```

Mantener las copias de seguridad de datos
habituales ya comprobadas; esta operación no cambia esquema ni contenido y no
requiere restaurar ni recrear PostgreSQL. No borrar imágenes, certificados o volúmenes.

## C. Actualización: misma sesión SSH y mismo lock

Cambiar solamente el valor de HTTPS_HOSTNAME, sin cargar el `.env` como código shell.
El siguiente bloque exige exactamente una asignación y conserva las demás líneas
y permisos. Evitar una variable exportada que sobreescriba el valor del archivo.

```bash
unset HTTPS_HOSTNAME
python3 - "$ROOT/compose/.env" "$NEW_HOST" <<'PY'
import os, pathlib, re, stat, sys, tempfile
path, host = pathlib.Path(sys.argv[1]), sys.argv[2]
data = path.read_bytes()
matches = list(re.finditer(rb'(?m)^([ \t]*(?:export[ \t]+)?HTTPS_HOSTNAME[ \t]*=[ \t]*)[^\r\n]*', data))
assert len(matches) == 1, 'Expected exactly one HTTPS_HOSTNAME assignment'
match = matches[0]
updated = data[:match.start()] + match[1] + host.encode('ascii') + data[match.end():]
fd, candidate = tempfile.mkstemp(prefix='.env.duckdns.', dir=path.parent)
with os.fdopen(fd, 'wb') as output:
    output.write(updated)
os.chmod(candidate, stat.S_IMODE(path.stat().st_mode))
os.replace(candidate, path)
PY
```

No tocar COMPOSE_PROJECT_NAME, credenciales, SIMULATOR_ENABLED, imágenes ni ninguna
otra variable. Validar sin obtener certificados; `compose run` no publica puertos
del servicio porque no se usa --service-ports:

```bash
dc config --quiet
dc run --rm --no-deps --pull never --entrypoint caddy caddy \
  validate --config /etc/caddy/Caddyfile --adapter caddyfile
cmp -s compose/images.env "$BACKUP/images.env"
cmp -s compose/compose.deploy.yml "$BACKUP/compose.deploy.yml"
cmp -s caddy/Caddyfile "$BACKUP/Caddyfile"
```

Recrear únicamente Caddy. Desde aquí el dominio antiguo deja de tener un sitio
activo. Puede haber una interrupción breve; si no se obtiene TLS sano en el plazo,
usar F y no repetir emisiones en bucle.

```bash
touch "$BACKUP/caddy-attempted"
dc up -d --no-deps --pull never caddy
tls_ready=false
for attempt in $(seq 1 30); do
  if curl --noproxy '*' --fail --silent --show-error --max-time 10 \
       --resolve "$NEW_HOST:443:127.0.0.1" "https://$NEW_HOST/" -o /dev/null; then
    tls_ready=true
    break
  fi
  sleep 5
done
test "$tls_ready" = true
```

El límite es 30 intentos, cada uno con timeout de 10 segundos más las pausas.
No usar `-k`. Una vez validado TLS, actualizar la URL pública del Backend:

```bash
touch "$BACKUP/backend-attempted"
dc up -d --no-deps --pull never backend
smoke_ready=false
for attempt in $(seq 1 30); do
  if bash scripts/smoke-https.sh "$NEW_HOST" "$LEGACY_IP"; then
    smoke_ready=true
    break
  fi
  sleep 5
done
test "$smoke_ready" = true
```

No recrear Frontend/PostgreSQL. La propiedad de URL pública se evalúa al arrancar
Backend: cambiar solo Caddy dejaría los enlaces de adjuntos con la base antigua.

## D. Verificación: SSH y después otra máquina/red

En la misma sesión SSH comprobar identidad de datos, imágenes y entorno:

```bash
test "$(dc ps -q postgres)" = "$(cat "$BACKUP/postgres.id")"
test "$(dc ps -q frontend)" = "$(cat "$BACKUP/frontend.id")"
snapshot_mounts > "$BACKUP/mounts.after"
diff -u "$BACKUP/mounts.before" "$BACKUP/mounts.after"
for service in caddy backend frontend; do
  test "$(docker inspect --format '{{.Image}}' "$(dc ps -q "$service")")" = "$(cat "$BACKUP/$service.image-id")"
done
docker inspect --format '{{json .Config.Env}}' "$(dc ps -q backend)" > "$BACKUP/backend-env.after.private.json"
```

Comparar el entorno sin imprimir secretos. Solo la URL pública debe cambiar:

```bash
python3 - "$BACKUP" "$NEW_HOST" <<'PY'
import json, pathlib, sys
root, host = pathlib.Path(sys.argv[1]), sys.argv[2]
def load(name):
    return dict(x.split('=', 1) for x in json.loads((root / name).read_text()))
before = load('backend-env.private.json')
after = load('backend-env.after.private.json')
before['M2_PUBLIC_BASE_URL'] = 'https://' + host
assert before == after, 'Backend runtime changed beyond public URL; investigate privately'
print('Backend runtime preserved, including SIMULATOR_ENABLED')
PY
bash scripts/smoke-https.sh "$NEW_HOST" "$LEGACY_IP"
dc ps
```

Inspeccionar certificado, sin leer claves privadas:

```bash
openssl s_client -connect 127.0.0.1:443 -servername "$NEW_HOST" \
  -verify_hostname "$NEW_HOST" -verify_return_error </dev/null \
  2>"$BACKUP/tls-verification.txt" | openssl x509 -noout -issuer -subject -dates -ext subjectAltName
```

En **otra máquina**, usar la resolución DNS normal (no --resolve) y repetir desde
otra red disponible. Estos son los controles públicos posteriores, no realizados
durante esta preparación:

```bash
NEW_HOST=atencion-ciudadana.duckdns.org
curl --noproxy '*' --fail --show-error --max-time 20 "https://$NEW_HOST/"
curl --noproxy '*' --fail --show-error --max-time 20 "https://$NEW_HOST/api/catalog/categories" \
  | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin),list)'
curl --noproxy '*' -sS --max-time 10 -D - -o /dev/null "http://$NEW_HOST/tickets?probe=cutover&n=1"
curl --noproxy '*' -sS --max-time 10 -D - -o /dev/null 'http://54.232.233.237/tickets?probe=cutover&n=1'
curl --noproxy '*' -sS --max-time 10 -X POST --data 'probe=cutover' -D - -o /dev/null "https://$NEW_HOST/api?probe=cutover&n=1"
```

Esperar React/API 200, HTTP hostname e IP con 308 a
`https://atencion-ciudadana.duckdns.org/tickets?probe=cutover&n=1`, y POST /api con
308 a `/api/?probe=cutover&n=1`. No usar -L para ocultar saltos intermedios.
Ningún Location debe apuntar a sslip.io ni HTTP desde una petición HTTPS.

En navegador: certificado válido/SAN correcto, login nuevo, ruta React profunda,
catálogo, ticket y descarga de un adjunto previo autorizado. En Network verificar
que las URLs públicas devueltas de adjuntos comiencen con el nuevo origen y que la
descarga conserve archivo/nombre. Probar carga/POST/PUT/PATCH solo con un ticket DEV
permitido y sin alterar datos reales por el smoke; las pruebas locales ya cubren
métodos, cuerpos binarios y cabeceras falsificadas. No copiar tokens a logs.

Si falla DNS público aunque --resolve funcione, la migración no está aceptada.
No confundir Caddy healthy con certificado confiable. Revisar logs acotados con
`dc logs --since=10m --tail=100 caddy backend` y no compartir datos sensibles.

## E. Finalización: solo después de aceptación

Con CD todavía false, retirar **nuestro** mantenimiento y liberar el lock. El script
normal no puede ejecutarse dentro del mismo lock ni con el sentinel activo:

```bash
rm -- "$DEPLOY_STATE/m2-maintenance"
flock -u 9
exec 9>&-
FRONTEND_IMAGE=$(awk -F= '$1=="FRONTEND_IMAGE" {sub(/\r$/, "", $2); print $2}' compose/images.env)
FRONTEND_SHA=${FRONTEND_IMAGE#ghcr.io/santimussi/front-desarrollo-apps-2:sha-}
[[ "$FRONTEND_SHA" =~ ^[0-9a-f]{40}$ ]]
bash scripts/deploy-service.sh frontend "$FRONTEND_SHA"
bash scripts/smoke-https.sh "$NEW_HOST" "$LEGACY_IP"
```

Esto prueba el flujo normal con el mismo SHA, sin solicitar otra versión. El script
consulta GHCR aunque la imagen exista; si falla por registro/red, mantener CD false,
investigar y reactivar mantenimiento antes de más cambios. No revertir un hostname
sano únicamente porque GHCR esté caído. No borrar imágenes para repetir la prueba.

Solo tras aceptación y autorización explícita para reactivar CD, en la terminal
administrativa:

```bash
gh variable set CD_ENABLED --repo SantiMussi/front-desarrollo-apps-2 --body true
gh variable set CD_ENABLED --repo JuanmaGuida/Backend-DAMII --body true
gh variable get CD_ENABLED --repo SantiMussi/front-desarrollo-apps-2
gh variable get CD_ENABLED --repo JuanmaGuida/Backend-DAMII
```

Registrar hostname, SHAs, proyecto, backup y fechas del certificado. Observar el
próximo CD autorizado de cada aplicación; no generar commits vacíos para probarlo.
Revisar posteriormente la renovación real: Caddy activo, mismos volúmenes, DNS y
80/443 disponibles, sin errores y con nuevas fechas cuando corresponda. No forzarla
borrando /data. Mantener acceso a la cuenta DuckDNS y comprobar el A si cambia la IP.

## F. Rollback a sslip.io: no cambia datos ni descarga imágenes

Mantener o volver a establecer CD false en ambos repositorios y drenar jobs como A.
Si sigue en la sesión con fd 9 bloqueado, **no volver a adquirirlo**: usar las
variables existentes y pasar al bloque de restauración. Si la sesión se perdió o
ya se liberó el lock, abrir SSH y ejecutar:

```bash
set -Eeuo pipefail
umask 077
ROOT=/home/ubuntu/DA2/Infra/DA2-m2-infra
cd "$ROOT"
OLD_HOST=54-232-233-237.sslip.io
LEGACY_IP=54.232.233.237
DEPLOY_STATE="$HOME/DA2/.deploy"
BACKUP=$(cat "$DEPLOY_STATE/duckdns-backup-path")
test -d "$BACKUP"
COMPOSE_PROJECT_NAME=$(cat "$BACKUP/project-name")
export COMPOSE_PROJECT_NAME
exec 9>"$DEPLOY_STATE/m2-deploy.lock"
flock -w 600 9
touch "$DEPLOY_STATE/m2-maintenance"
```

Definir `dc` como en B si es una sesión nueva. Verificar que las imágenes anteriores
siguen en caché y restaurar la configuración respaldada sin imprimirla:

```bash
for service in caddy backend frontend; do
  docker image inspect "$(cat "$BACKUP/$service.image-id")" >/dev/null
done
restore_file() {
  if ! cmp -s "$1" "$2"; then
    cp -p -- "$1" "$2.duckdns-restore"
    mv -f -- "$2.duckdns-restore" "$2"
  fi
}
restore_file "$BACKUP/runtime.env.private" compose/.env
restore_file "$BACKUP/compose.deploy.yml" compose/compose.deploy.yml
restore_file "$BACKUP/Caddyfile" caddy/Caddyfile
restore_file "$BACKUP/images.env" compose/images.env
unset HTTPS_HOSTNAME
dc config --quiet
```

Recrear solamente los servicios cuya actualización se intentó. Así un fallo de
preparación previo a C no provoca reinicios adicionales. Las marcas se guardaron
antes de cada intento para cubrir fallos parciales de Compose.

```bash
if test -e "$BACKUP/caddy-attempted"; then
  dc up -d --no-deps --pull never caddy
fi
if test -e "$BACKUP/backend-attempted"; then
  dc up -d --no-deps --pull never backend
fi
rollback_ready=false
for attempt in $(seq 1 30); do
  if bash scripts/smoke-https.sh "$OLD_HOST" "$LEGACY_IP"; then
    rollback_ready=true
    break
  fi
  sleep 5
done
test "$rollback_ready" = true
test "$(dc ps -q postgres)" = "$(cat "$BACKUP/postgres.id")"
test "$(dc ps -q frontend)" = "$(cat "$BACKUP/frontend.id")"
```

Definir `snapshot_mounts` como en B si es una sesión nueva y repetir D con
`NEW_HOST="$OLD_HOST"` para comparar montajes y entorno contra la URL restaurada.
Verificar externamente HTTPS sslip.io, login y el adjunto previo. Mantener CD false hasta
aceptar la recuperación. Si termina la sesión de diagnóstico, liberar fd 9 pero
conservar el sentinel hasta resolver el incidente; retirarlo conscientemente antes
de reactivar CD. Un backup completo restaura también cualquier otra variable privada
original; no copiar ejemplos ni restaurar la base de datos.

Los volúmenes Caddy se conservan: el certificado anterior puede reutilizarse si
sigue vigente; si venció o se perdió, haría falta emisión nueva y no se garantiza
recuperación sin CA/DNS. Comprobar su vigencia antes de la ventana, no meses después.
No usar down, prune, rm de certificados/imágenes, --remove-orphans ni Terraform apply.

## Qué sucede con la URL antigua

Tras recrear Caddy, solo está configurado DuckDNS y la IP HTTP. El antiguo HTTPS
no tiene sitio ni redirección garantizados: puede fallar TLS o no ser atendido.
Conservar su certificado en disco no conserva su servicio ni garantiza renovación
mientras el nombre no esté configurado. La IP HTTP redirige al nuevo dominio.

No se implementa compatibilidad simultánea: requeriría dos sitios y mantener TLS
para ambos. Si enlaces antiguos o usuarios necesitan una transición sin corte,
detenerse y acordar ese alcance antes de migrar. Notificar previamente la URL nueva.
HSTS/cache del navegador no redirige de un hostname a otro; puede exigir HTTPS al
antiguo y conservar redirecciones 308. Probar con una sesión limpia y el dominio
correcto; no deshabilitar validación TLS ni depender de la IP HTTP como rollback TLS.

## Validación de esta preparación

No se modificó configuración funcional ni el ejemplo que sigue describiendo el
hostname actual. Se amplió el fixture de tests para pasar el hostname configurado,
probar smoke/CD simulado con DuckDNS, Actuator v3+json y rechazo de redirecciones al
hostname anterior/certificados no confiables simulados. Los proxies reales locales
se ejercitan para ambos dominios sobre loopback con backend eco y sin ACME.

Compose se valida con datos dummy y ambos hostnames, comparando que solo cambien
el hostname de Caddy y la URL pública de Backend, sin cambios de puertos, imágenes,
redes, volúmenes o otras variables runtime. Caddy validate no solicita certificados.
Estas pruebas no son una emisión pública, un login real desplegado ni la observación
de una renovación. Esos controles quedan explícitamente para D/E después de A.

Resultados locales: 14 tests de scripts correctos en Linux/WSL y 10 tests de proxies
reales correctos en loopback (cinco por hostname); Compose local/deploy y comparación
de ambos hostnames correctos con simulador true/false; Caddy 2.11.7 validate correcto
sin emisión. No se arrancó un stack de aplicación ni se modificaron contenedores,
datos o volúmenes existentes. La integración real con el dominio público sigue
pendiente del control CAA y de la ventana autorizada.
