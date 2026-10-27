# HTTPS público: activación, mantenimiento y reversión

Esta guía describe la activación manual posterior a integrar HTTPS en `dev` y
publicar la imagen Frontend por SHA. Integrar código o publicar una imagen no activa
HTTPS: no se debe acceder a Lightsail ni modificar AWS durante esa integración.
Los comandos de este documento son para que el operador los ejecute posteriormente.

## Diseño y límites

`HTTPS navegador → Caddy :443 → frontend/Nginx :8082 → backend :8080 → postgres :5432`.
Caddy publica exclusivamente TCP 80/443, redirige HTTP a HTTPS y obtiene/renueva
certificados de Let's Encrypt. Imagen oficial fijada: `caddy:2.11.7-alpine`.
`HTTPS_HOSTNAME` es obligatorio en deploy; inicialmente `54-232-233-237.sslip.io`.
`LEGACY_HTTP_IP=54.232.233.237` tiene un sitio **sólo HTTP** con redirección 308
al hostname canónico, conservando URI y query. No se solicita certificado para la IP.
Caddy no agrega HSTS. Spring Security conserva su comportamiento existente y,
al reconocer HTTPS, agrega HSTS en respuestas de la API. Para revertir, usar la
dirección anterior `http://54.232.233.237`: un navegador que haya recibido HSTS
puede seguir exigiendo HTTPS para el hostname sslip.io, incluso después de revertir.

Caddy conserva URI, método y cuerpo; fija Host/protocolo/puerto y reemplaza la IP
del cliente desde la conexión real. Elimina `Forwarded` y `X-Forwarded-Prefix`.
Nginx distingue sus listeners: 80 ignora cabeceras de proxy (desarrollo HTTP),
8082 representa exclusivamente la entrada privada HTTPS desde Caddy. **Nunca publicar
8082** ni agregar un proxy público alternativo que permita saltarse Caddy. La red
Docker y el acceso root/loopback del servidor son el límite de confianza; los
contenedores y usuarios con acceso a esa red deben ser confiables.
Las redirecciones de Nginx son relativas; `/api?x=1` devuelve 308 a `/api/?x=1`.
308 conserva método y cuerpo en clientes compatibles. Los formularios usan `/api/...`
directamente. Nginx mantiene `client_max_body_size 55m` y el proxy de adjuntos.

Compose activa `SERVER_FORWARD_HEADERS_STRATEGY=framework` sólo en deploy y fija
`M2_PUBLIC_BASE_URL=https://${HTTPS_HOSTNAME}`: Spring interpreta el origen y genera
URLs de adjuntos públicas correctas. No hay cambios de código Java. Backend mantiene
`127.0.0.1:8080`; PostgreSQL no publica puertos. No se modifica `compose.yml` local.
La nueva red `edge` une Caddy con Frontend; la red predeterminada y los volúmenes
`postgres_data` y `attachment_data` conservan su identidad. No se fija un nombre de
proyecto nuevo: se debe recuperar y conservar el efectivo del servidor.
No se altera la Static IP ni la salida del Backend, incluyendo AMQPS TCP 5671.

Requisitos externos que todavía deben comprobarse:

- DNS A debe resolver el hostname a `54.232.233.237` desde resolvers públicos y los
  clientes. No publicar AAAA salvo que apunte a la IPv6 real accesible de esta instancia.
- Entrada TCP 80 y 443 accesible a Internet, sin otro proceso ocupándolos; salida
  DNS y HTTPS a la CA, reloj del servidor correcto y espacio de disco suficiente.
- sslip.io es un servicio gratuito de terceros, sin control propio sobre DNS,
  disponibilidad, filtros de resolvers o bloqueos por abuso. Puede haber límites de
  emisión por dominio compartido, cuenta, hostname y validaciones fallidas. No se
  garantiza la emisión, aunque DNS resuelva correctamente.
- A fecha de consulta, nip.io y sslip.io comparten operación. `54-232-233-237.nip.io`
  es una alternativa manual sencilla; no garantiza independencia ante una caída DNS.
  Un dominio propio da control DNS, pero su registro puede tener costo: no se compra
  ni configura automáticamente. No se usan wildcard, DNS-01 ni servicios pagos.
- Si la CA devuelve rate limit, respetar `Retry-After`: no borrar volúmenes ni
  provocar reemisiones en bucle. Staging sirve para un ensayo separado y **no**
  supera los smoke tests públicos de esta entrega.

Fuentes oficiales consultadas: [servicio DNS y límites](https://nip.io/),
[límites Let's Encrypt](https://letsencrypt.org/docs/rate-limits/),
[HTTPS automático Caddy](https://caddyserver.com/docs/automatic-https),
[cabeceras del reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy),
[versión estable](https://github.com/caddyserver/caddy/releases/tag/v2.11.7).

## 1. Preparar publicación y sincronización (más adelante)

Primero establecer `CD_ENABLED=false` en **ambos** repositorios de aplicación en
GitHub y esperar a que todos los jobs deploy en ejecución o en cola terminen o se
cancelen. La publicación GHCR y el CI siguen funcionando con CD deshabilitado.
Usar el SHA completo del commit de integración Frontend cuyo CI y publicación GHCR
hayan terminado correctamente. Verificar el tag `sha-<40 hex>` y que el job deploy
figure como omitido (`skipped`); no basta con que el commit exista en GitHub.
La imagen debe contener este `nginx.conf` y su listener privado 8082.
Commit Frontend de esta integración: `4af56deb35952ac2f58aee6d6e81b7ec7bab884b`;
tag esperado: `ghcr.io/santimussi/front-desarrollo-apps-2:sha-4af56deb35952ac2f58aee6d6e81b7ec7bab884b`.
Backend no necesita una nueva imagen por HTTPS. Mantener `CD_ENABLED=false` hasta
completar la aceptación manual del paso 7; no modificar variables ni secretos como
parte de la integración Git.

Preparar un paquete exclusivamente de Infra desde un commit validado de `dev`.
`git archive` excluye archivos sin commit, `.env`, `images.env`, credenciales, datos
y binarios de pruebas. Conservar el SHA de Infra junto al SHA Frontend:

```powershell
Set-Location C:\dev\DA2\Infra\DA2-m2-infra
New-Item -ItemType Directory -Path .https-validation -Force | Out-Null
$infraSha = (git rev-parse dev).Trim()
git archive --format=tar --output=.https-validation/infra-https.tar $infraSha compose/compose.deploy.yml compose/.env.example caddy/Caddyfile scripts/deploy-service.sh scripts/smoke-https.sh README.md docs/deploy-aws-dev.md docs/runbook.md docs/https.md docs/https-validation.md
if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el paquete de Infra' }
Write-Output "Infra SHA: $infraSha"
# Usar la identidad SSH y known_hosts verificados de la administración existente.
scp -o StrictHostKeyChecking=yes .https-validation/infra-https.tar ubuntu@54.232.233.237:/home/ubuntu/infra-https.tar
```

No ejecutar aún el script nuevo de deploy: exige HTTPS sano antes de cambiar imágenes.
No extraer el paquete sobre el runtime hasta guardar la configuración HTTP anterior.

## 2. Firewall y DNS

En Lightsail → instancia `m2-dev` → Networking:

- Conservar TCP 80 público (`0.0.0.0/0`) para HTTP y ACME HTTP-01.
- **Agregar HTTPS TCP 443** público (`0.0.0.0/0`).
- Conservar SSH TCP 22 restringido a los orígenes administrativos existentes.
- Si se usa IPv6, mantener 80 y agregar 443 en el firewall IPv6 (`::/0`);
  no crear AAAA del hostname IPv4. No abrir UDP 443: HTTP/3 está deshabilitado.
- No abrir 8080, 8082, 5432, 2019, 5671 ni otros puertos de entrada.

Si UFW u otro firewall del host está activo, agregar allí TCP 80/443 siguiendo su
política existente. No vaciar reglas ni deshabilitar el firewall. Docker puede
gestionar reglas propias: comprobar también desde una máquina externa.
`terraform/main.tf` refleja 443, pero **no ejecutar apply**: el recurso manual
requiere importación y revisión de state en una tarea independiente.

Desde una terminal administrativa y después desde el servidor:

```bash
dig +short A 54-232-233-237.sslip.io
dig +short AAAA 54-232-233-237.sslip.io
dig +short CAA 54-232-233-237.sslip.io
# Si hay discrepancias, contrastar con el resolver público permitido por tu red.
dig @1.1.1.1 +short A 54-232-233-237.sslip.io
```

Esperar A=54.232.233.237; investigar AAAA inesperado, SERVFAIL, NXDOMAIN, CAA que
impida Let's Encrypt o filtros antes de cortar HTTP. `dig` requiere `dnsutils` en Ubuntu.

## 3. Guardar estado HTTP y bloquear deploys

Conectarse manualmente usando la configuración SSH administrativa existente. Mantener
la **misma sesión Bash** durante los pasos 3–7; si se corta, no declarar éxito y
revisar estado antes de continuar. No ejecutar simultáneamente otros procedimientos.

```bash
set -Eeuo pipefail
ROOT=/home/ubuntu/DA2/Infra/DA2-m2-infra
cd "$ROOT"
umask 077
mkdir -p /home/ubuntu/DA2/.deploy
chmod 700 /home/ubuntu/DA2/.deploy
exec 9>/home/ubuntu/DA2/.deploy/m2-deploy.lock
flock -w 600 9
touch /home/ubuntu/DA2/.deploy/m2-maintenance

# Elegir el Backend de ESTE proyecto si hay más de un stack en Docker.
docker ps --filter label=com.docker.compose.service=backend --format 'table {{.ID}}\t{{.Names}}'
read -rp 'ID del Backend M2 vigente: ' BACKEND_CONTAINER
COMPOSE_PROJECT_NAME=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$BACKEND_CONTAINER")
WORKING_DIR=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$BACKEND_CONTAINER")
test "$WORKING_DIR" = "$ROOT/compose"
test -n "$COMPOSE_PROJECT_NAME"
export COMPOSE_PROJECT_NAME
printf 'Proyecto existente: %s\n' "$COMPOSE_PROJECT_NAME"

dc() {
  docker compose --project-directory "$ROOT/compose" \
    --env-file "$ROOT/compose/.env" --env-file "$ROOT/compose/images.env" \
    -f "$ROOT/compose/compose.deploy.yml" "$@"
}
dc_candidate() {
  docker compose --project-directory "$ROOT/compose" \
    --env-file "$ROOT/compose/.env" --env-file "$CANDIDATE" \
    -f "$ROOT/compose/compose.deploy.yml" "$@"
}
dc config --quiet
BACKUP=/home/ubuntu/DA2/.deploy/https-http-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -m 700 "$BACKUP"
cp -p compose/compose.deploy.yml "$BACKUP/compose.deploy.yml"
cp -p scripts/deploy-service.sh "$BACKUP/deploy-service.sh"
cp -p compose/images.env "$BACKUP/images.env"
# Sólo resguardo privado: no se imprime ni se reemplaza .env con un ejemplo.
cp -p compose/.env "$BACKUP/runtime.env.private"
printf '%s\n' "$COMPOSE_PROJECT_NAME" > "$BACKUP/project-name"
docker inspect --format '{{range .Mounts}}{{println .Type .Name .Destination}}{{end}}' \
  "$(dc ps -q postgres)" "$(dc ps -q backend)" > "$BACKUP/data-mounts.before"
printf '%s\n' "$BACKUP" > /home/ubuntu/DA2/.deploy/https-backup-path
dc ps
curl --noproxy '*' --fail --max-time 10 http://127.0.0.1:8080/actuator/health

# Respaldo de datos, además de configuración: se ejecuta sólo en la migración.
# Pausar escrituras de usuarios antes de ambos respaldos y hasta verificar el corte
# para mantener coherencia entre referencias en PostgreSQL y archivos adjuntos.
dc exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$BACKUP/postgres.dump"
dc exec -T backend tar -C /var/lib/m2/attachments -cf - . > "$BACKUP/attachments.tar"
test -s "$BACKUP/postgres.dump"
test -s "$BACKUP/attachments.tar"
dc exec -T postgres pg_restore --list < "$BACKUP/postgres.dump" > "$BACKUP/postgres.contents"
tar -tf "$BACKUP/attachments.tar" > "$BACKUP/attachments.contents"
chmod 600 "$BACKUP/"*
```

Si la etiqueta working_dir difiere, detenerse e investigar el proyecto efectivo;
no elegir automáticamente otro nombre ni arrancar PostgreSQL de nuevo.
Comprobar previamente la restauración de los respaldos en un entorno aislado;
no restaurarlos sobre los datos vigentes como parte de la activación o reversión.
El backup contiene secretos privados y datos: conservarlo con permisos 700/600 fuera del repo,
no subirlo ni adjuntarlo a un issue. El lock protege contra CD entre repositorios;
el sentinel también bloquea scripts nuevos después de terminar esta sesión.

## 4. Publicar primero el Frontend compatible, aún por HTTP

Con el Compose **HTTP anterior** todavía instalado y el lock tomado:

```bash
read -rp 'SHA completo Frontend nuevo ya publicado en GHCR: ' FRONTEND_SHA
[[ "$FRONTEND_SHA" =~ ^[0-9a-f]{40}$ ]]
CANDIDATE=$(mktemp "$ROOT/compose/.images.env.candidate.XXXXXX")
python3 - "$ROOT/compose/images.env" "$CANDIDATE" "$FRONTEND_SHA" <<'PY'
from pathlib import Path
import sys
source, target, sha = sys.argv[1:]
lines = Path(source).read_text().splitlines()
key = 'FRONTEND_IMAGE='
assert sum(x.startswith(key) for x in lines) == 1, 'Expected one FRONTEND_IMAGE'
Path(target).write_text('\n'.join(key + 'ghcr.io/santimussi/front-desarrollo-apps-2:sha-' + sha
                                if x.startswith(key) else x for x in lines) + '\n')
PY
chmod --reference=compose/images.env "$CANDIDATE"
dc_candidate pull frontend
dc_candidate up -d --no-deps frontend
dc_candidate exec -T frontend nginx -t
dc_candidate exec -T frontend wget -q -O /dev/null http://127.0.0.1:8082/
test "$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1/)" = 200
curl --noproxy '*' -fsS --max-time 10 http://127.0.0.1/api/catalog/categories | \
  python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin),list)'
# Promover sólo después de verificar; conserva el SHA Backend y demás líneas.
mv -f "$CANDIDATE" compose/images.env
```

Si falla cualquier comprobación: **no promover el candidato**. Recuperar inmediatamente
el Frontend con `dc up -d --no-deps --pull never frontend` usando images.env
anterior, repetir HTTP/API y detener la migración. Conservar el CD deshabilitado.
La imagen anterior debe permanecer en la caché local; no hacer prune ni eliminarla.
La recuperación no debe depender de que GHCR esté disponible durante el incidente.
No ejecutar `deploy-service.sh` dentro de este lock: intentaría tomarlo otra vez.

## 5. Instalar Infra y preparar Caddy sin liberar todavía el 80

```bash
STAGE=/home/ubuntu/DA2/.deploy/https-stage
mkdir -p "$STAGE"
tar -tf /home/ubuntu/infra-https.tar
# Debe contener sólo los archivos de la lista del paso 1.
tar -xf /home/ubuntu/infra-https.tar -C "$STAGE"
mkdir -p "$ROOT/caddy"
install -m 644 "$STAGE/compose/compose.deploy.yml" compose/compose.deploy.yml
install -m 644 "$STAGE/compose/.env.example" compose/.env.example
install -m 644 "$STAGE/caddy/Caddyfile" caddy/Caddyfile
install -m 755 "$STAGE/scripts/deploy-service.sh" scripts/deploy-service.sh
install -m 755 "$STAGE/scripts/smoke-https.sh" scripts/smoke-https.sh
install -m 644 "$STAGE/README.md" README.md
install -m 644 "$STAGE/docs/"*.md docs/
# Editar el archivo existente; NO copiar .env.example encima.
nano compose/.env
```

Agregar/actualizar exactamente una vez en el `.env` privado:

```dotenv
HTTPS_HOSTNAME=54-232-233-237.sslip.io
LEGACY_HTTP_IP=54.232.233.237
COMPOSE_PROJECT_NAME=<el proyecto efectivo mostrado en el paso 3>
```

Conservar todas las passwords, variables runtime existentes y almacenamiento,
incluido el valor actual de `SIMULATOR_ENABLED` (sea `true` o `false`). HTTPS no
habilita ni deshabilita el simulador: no modificar esa variable ni copiar el valor
del archivo de ejemplo. `FRONTEND_PORT` puede conservar su valor anterior:
deploy HTTPS lo ignora; el Compose local sigue usándolo. No configurar un
`M2_PUBLIC_BASE_URL` HTTP alternativo: deploy lo deriva del hostname HTTPS.

```bash
chmod 600 compose/.env compose/images.env
for script in scripts/*.sh; do bash -n "$script"; done
dc config --quiet
dc pull caddy
dc run --rm --no-deps --entrypoint caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
# Ver sólo puertos y estado, nunca renderizar el config completo con secretos.
dc ps
sudo ss -ltnp '( sport = :80 or sport = :443 )'
```

El 80 todavía debe pertenecer al Frontend HTTP vigente; 443 debe estar libre.
La validación de Caddy no obtiene certificados ni demuestra conectividad con la CA.

## 6. Activar HTTPS y comprobarlo

Esta es la ventana breve de interrupción: Frontend se recrea sin publicar 80,
y luego Caddy puede ocuparlo. No arrancar ambos en orden inverso.

```bash
dc up -d --no-deps frontend
dc up -d --no-deps caddy
dc logs --tail=100 caddy
# Esperar emisión inicial con un plazo acotado, sin -k y sin reiniciar Caddy en bucle.
HTTPS_READY=false
for attempt in $(seq 1 30); do
  if bash scripts/smoke-https.sh 54-232-233-237.sslip.io 54.232.233.237; then
    HTTPS_READY=true
    break
  fi
  sleep 5
done
test "$HTTPS_READY" = true
# Incorporar cabeceras originales y base pública de adjuntos al Backend existente.
# Recrea sólo Backend; no toca PostgreSQL ni su volumen.
dc up -d --no-deps backend
HTTPS_READY=false
for attempt in $(seq 1 30); do
  if bash scripts/smoke-https.sh 54-232-233-237.sslip.io 54.232.233.237; then
    HTTPS_READY=true
    break
  fi
  sleep 5
done
test "$HTTPS_READY" = true
docker inspect --format '{{range .Mounts}}{{println .Type .Name .Destination}}{{end}}' \
  "$(dc ps -q postgres)" "$(dc ps -q backend)" > "$BACKUP/data-mounts.after"
diff -u "$BACKUP/data-mounts.before" "$BACKUP/data-mounts.after"
dc ps
```

Cada fallo detiene la secuencia: mantener CD apagado y usar la reversión del paso 8
si no puede corregirse dentro de la ventana. La emisión puede tardar más que estos
reintentos; revisar causa y decidir explícitamente, sin declarar éxito por el mero
estado `running` o una redirección HTTP. `--resolve host:443:127.0.0.1` valida el
nombre/SNI, cadena pública y expiración sin depender del acceso a la propia IP.
Los smoke tests exigen health JSON UP, HTML React, catálogo JSON array con 200,
redirecciones 308 con destino exacto y query preservada, incluyendo `/api` y la IP.

Desde **otra máquina**, comprobar DNS, firewall y certificado públicos:

```bash
curl --noproxy '*' --fail --show-error --max-time 20 https://54-232-233-237.sslip.io/
curl --noproxy '*' --fail --show-error --max-time 20 https://54-232-233-237.sslip.io/api/catalog/categories
curl --noproxy '*' --head --max-time 10 'http://54-232-233-237.sslip.io/api?x=1'
curl --noproxy '*' --head --max-time 10 'http://54.232.233.237/tickets?x=1'
curl --noproxy '*' --head --max-time 10 'https://54-232-233-237.sslip.io/api?x=1'
```

Esperar 200 en React/API y 308 a HTTPS en HTTP. `/api` sobre HTTPS debe devolver
`Location: /api/?x=1`. Inspeccionar certificado en navegador (SAN=hostname, cadena
confiable y vigencia), recargar una ruta React profunda y comprobar consola Network
sin mixed content ni saltos a HTTP. El navegador tendrá un origen nuevo: el login
guardado en localStorage de la IP HTTP no se transfiere; iniciar sesión de nuevo.

Probar con un ticket DEV permitido: POST de creación, PUT/PATCH aplicables, carga
multipart de un adjunto permitido y descarga autenticada/anónima según corresponda.
Comprobar que URLs devueltas empiezan con el hostname HTTPS, que un adjunto previo
sigue accesible y que descarga conserva bytes/nombre. El límite Nginx sigue en
55 MiB; el límite de archivos del Backend sigue en 10 MB cada uno / 50 MB total
según su configuración vigente. No crear ni alterar datos productivos sólo para smoke.
Probar los futuros flujos M1 y URLs de retorno cuando esa integración se habilite;
este cambio no introduce autenticación ni modifica Analytics o reglas de negocio.

## 7. Habilitar CD después de aceptación

Guardar el SHA Frontend compatible como primer punto de rollback HTTPS. Imágenes
Frontend anteriores sin listener 8082 **no son un rollback HTTPS válido**: su fallo
no se arregla cambiando sólo un SHA. Backend puede volver a imágenes compatibles
con el esquema actual, sin cambiar sus migraciones ni datos como parte de HTTPS.

```bash
# Sigue en la sesión que tiene fd 9 bloqueado.
rm -- /home/ubuntu/DA2/.deploy/m2-maintenance
flock -u 9
exec 9>&-
# Verificar la interfaz CD normal sin cambiar versiones: usar los SHAs actuales sanos.
bash scripts/deploy-service.sh frontend "$FRONTEND_SHA"
# Opcional: repetir con el SHA Backend vigente, leído de images.env sin imprimir secretos.
```

Si el script falla, no habilitar CD. Una prueba real de rollback exige disponer de
dos SHAs Frontend compatibles; hacerla en una ventana controlada. Los tests locales
de scripts cubren fallos y rollback con simuladores, no sustituyen esa aceptación.
Sólo después de la aceptación y con autorización explícita para reactivar CD,
restablecer `CD_ENABLED=true` en ambos repositorios. Sus workflows siguen
publicando por SHA, desplegando independientemente y compartiendo `flock`.

## 8. Revertir la infraestructura HTTPS a HTTP

Esto es distinto del rollback automático de una **imagen**. El script de CD no
restaura infraestructura, no reemite certificados y no arregla DNS/firewall.
Para el rollback automático de imagen, reutiliza primero el SHA anterior en caché;
sólo consulta GHCR si falta y siempre repite las comprobaciones HTTPS.
Antes de revertir: CD apagado en ambos repos y jobs drenados. Si la sesión anterior
se cortó, recuperar el proyecto y backup y tomar el mismo lock:

```bash
set -Eeuo pipefail
ROOT=/home/ubuntu/DA2/Infra/DA2-m2-infra
cd "$ROOT"
BACKUP=$(cat /home/ubuntu/DA2/.deploy/https-backup-path)
COMPOSE_PROJECT_NAME=$(cat "$BACKUP/project-name")
export COMPOSE_PROJECT_NAME
exec 9>/home/ubuntu/DA2/.deploy/m2-deploy.lock
flock -w 600 9
touch /home/ubuntu/DA2/.deploy/m2-maintenance
dc() {
  docker compose --project-directory "$ROOT/compose" \
    --env-file "$ROOT/compose/.env" --env-file "$ROOT/compose/images.env" \
    -f "$ROOT/compose/compose.deploy.yml" "$@"
}
# Ejecutar con Compose HTTPS todavía instalado; libera 80/443 primero.
# Verificar caché ANTES de detener el ingreso: una caída de GHCR no debe bloquear
# la reversión. Los tags deben ser los SHAs que se guardaron antes de migrar.
for image_key in BACKEND_IMAGE FRONTEND_IMAGE; do
  previous_image=$(awk -v key="$image_key" 'index($0,key "=")==1 {value=substr($0,length(key)+2); sub(/\r$/,"",value); print value}' "$BACKUP/images.env")
  test -n "$previous_image"
  docker image inspect "$previous_image" >/dev/null
done
dc stop caddy
install -m 644 "$BACKUP/compose.deploy.yml" compose/compose.deploy.yml
install -m 755 "$BACKUP/deploy-service.sh" scripts/deploy-service.sh
# Restauración transaccional de referencias anteriores, no un archivo de ejemplo.
cp -p "$BACKUP/images.env" compose/.images.env.restore
mv -f compose/.images.env.restore compose/images.env
# .env privado se conserva: hostname/project extra son inofensivos para Compose HTTP.
# Si FRONTEND_PORT se cambió manualmente, volver a 80 en el .env existente.
dc config --quiet
dc up -d --no-deps --pull never frontend
# Sólo necesario si se recreó Backend con variables de HTTPS:
dc up -d --no-deps --pull never backend
for attempt in $(seq 1 30); do
  if curl --noproxy '*' -fsS --max-time 10 http://127.0.0.1:8080/actuator/health | \
       python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "UP"' && \
     test "$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1/)" = 200 && \
     curl --noproxy '*' -fsS --max-time 10 http://127.0.0.1/api/catalog/categories | \
       python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin),list)'; then
    break
  fi
  test "$attempt" -lt 30
  sleep 5
done
dc ps
docker inspect --format '{{range .Mounts}}{{println .Type .Name .Destination}}{{end}}' \
  "$(dc ps -q postgres)" "$(dc ps -q backend)" > "$BACKUP/data-mounts.restored"
diff -u "$BACKUP/data-mounts.before" "$BACKUP/data-mounts.restored"
flock -u 9
exec 9>&-
```

Si todavía se tiene fd 9 bloqueado en la sesión de migración, ejecutar directamente
desde la verificación de caché del bloque anterior en esa sesión, sin intentar
adquirir el lock de nuevo.
Si la migración falló antes de instalar el Compose HTTPS, no ejecutar `dc stop caddy`:
restaurar sólo Frontend con el Compose HTTP y las imágenes anteriores.
Comprobar externamente `http://54.232.233.237/`, API y adjuntos. El contenedor Caddy
queda detenido; conservar sus volúmenes para futuros intentos. El Compose anterior
puede avisar de ese contenedor huérfano: no usar `--remove-orphans` por reflejo.
No usar `down`, `down -v`, `prune` ni recrear PostgreSQL. Mantener CD apagado hasta
aceptar HTTP nuevamente. El script HTTP antiguo no conoce el sentinel: CD=false y
el lock son imprescindibles durante la recuperación. Retirar el sentinel sólo tras
resolver el incidente; habilitar CD HTTP exige mantener el script/Compose HTTP juntos.
No exigir un pull de GHCR para recuperar HTTP: las imágenes anteriores ya debían
estar presentes antes del corte. Si faltan, detenerse y recuperar los SHAs correctos;
no sustituirlos por imágenes o tags no verificados. El hostname HTTPS puede seguir
sujeto a HSTS del navegador; verificar la reversión mediante la IP HTTP anterior.

## Renovación, hostname propio y diagnóstico

La renovación la ejecuta Caddy en segundo plano; no requiere cron ni Certbot. Mantener
Caddy arrancado, volúmenes `caddy_data:/data` (claves/certificados/cuenta ACME) y
`caddy_config:/config`, hostname, DNS y puertos 80/443 estables. No borrar esos datos.
No guardar claves privadas en Git ni copiar `/data` a un artefacto público.
El healthcheck sólo consulta la API administrativa **loopback del contenedor** en
2019; ese puerto no se publica. Un healthcheck sano no demuestra certificado válido.

```bash
cd /home/ubuntu/DA2/Infra/DA2-m2-infra
# Definir dc como en el paso 3, conservando COMPOSE_PROJECT_NAME.
dc ps caddy
dc logs --since=24h --tail=100 caddy
docker inspect --format '{{range .Mounts}}{{println .Name .Destination}}{{end}}' "$(dc ps -q caddy)"
bash scripts/smoke-https.sh 54-232-233-237.sslip.io 54.232.233.237
# Ver fechas/SAN/issuer sin leer claves privadas (el smoke anterior valida confianza).
openssl s_client -connect 127.0.0.1:443 -servername 54-232-233-237.sslip.io \
  -verify_hostname 54-232-233-237.sslip.io -verify_return_error </dev/null 2>/dev/null | \
  openssl x509 -noout -issuer -subject -dates -ext subjectAltName
```

Confirmar persistencia de ambos mounts, ausencia de errores de mantenimiento y vigencia.
Repetir smoke y fechas periódicamente; la renovación efectiva sólo queda demostrada
cuando ocurra y se observe el nuevo certificado. No forzar emisión borrando `/data`.
Docker aplica restart al salir/reiniciar el daemon; un contenedor unhealthy por sí
solo no se reinicia automáticamente. Revisar logs, disco, DNS, reloj y CA ante fallos.
Los logs están rotados a 10 MB × 3 por servicio. No se agregan access logs con datos
de consultas/tokens; los diagnósticos existentes deben revisarse antes de compartir.

Para cambiar a un dominio propio o a nip.io: apagar CD, drenar jobs, tomar el lock y
sentinel, preparar/verificar A/AAAA/CAA y firewall, editar únicamente HTTPS_HOSTNAME
en el `.env` existente, `dc config --quiet`, validar Caddy como en paso 5 y
`dc up -d --no-deps caddy` (recrear, porque cambió su entorno). Esperar certificado
confiable usando `bash scripts/smoke-https.sh NUEVO_HOST 54.232.233.237`, luego
`dc up -d --no-deps backend` para actualizar las URLs de adjuntos, esperar health
y repetir smoke externo/local. No cambiar FRONTEND_IMAGE ni M2_PUBLIC_BASE_URL
en código. La IP HTTP redirigirá automáticamente al hostname nuevo. No hay
redirección del hostname HTTPS anterior: conservarla requeriría configuración y
certificado adicionales, fuera de este cambio mínimo. Revertir el hostname previo
y recrear Caddy/Backend si falla. Retirar sentinel/liberar lock y reactivar CD sólo
tras aceptar; conservar siempre los mismos volúmenes y proyecto.

Diagnóstico rápido:

- DNS errado/bloqueado: revisar A/AAAA/CAA, filtros del resolver y estado del proveedor;
  `--resolve` no demuestra DNS público ni que una CA pueda llegar al servidor.
- ACME timeout/unauthorized: comprobar 80/443 en ambos firewalls, conflictos de puertos,
  DNS y reloj. No publicar 2019 ni deshabilitar TLS para que el smoke pase.
- `rateLimited`: respetar la espera indicada. Evaluar cambio manual de hostname/
  proveedor/dominio; no reiniciar en bucle ni borrar cuentas/certificados.
- TLS correcto pero 502: comprobar que la imagen Frontend tenga listener 8082,
  `dc exec -T frontend nginx -t`, `dc logs --tail=100 frontend caddy` y red edge.
- API falla pero Actuator UP: revisar Nginx, resolución Docker `backend`, headers y
  ruta `/api`; no considerar React fallback como API sana.
- Enlaces de adjuntos HTTP/localhost: recrear sólo Backend con el Compose de HTTPS,
  comprobar hostname y su base pública; no mover ni borrar archivos existentes.
- Puerto ocupado: recrear Frontend sin publicación antes de iniciar Caddy. No matar
  procesos desconocidos ni usar `down` para liberar el puerto.
- Una imagen falla: rollback automático por SHA. Una CA/DNS/proxy falla: mantener
  el gate y diagnosticar/revertir infraestructura; no iterar SHAs sin causa.
