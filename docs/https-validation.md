# Entrega HTTPS — validación local del 21/10/2026

Se trabajó sobre las ramas `dev` existentes de los tres repositorios. No se crearon
ramas, commits ni pushes. No se establecieron conexiones con Lightsail ni se
modificó AWS, `.env`, `images.env`, datos o volúmenes de aplicación.
Los documentos funcionales adjuntos no se interpretaron como instrucciones de
infraestructura; se siguió el texto específico solicitado por el usuario.
Los archivos previamente sin seguimiento de Backend/Frontend se conservaron.

## Archivos y finalidad

| Repositorio / archivo | Cambio |
| --- | --- |
| Infra `compose/compose.deploy.yml` | Caddy 2.11.7 oficial; 80/443 públicos; Frontend privado; volúmenes TLS; red edge; Spring forwarded headers y base pública de adjuntos por entorno. |
| Infra `caddy/Caddyfile` | TLS público automático Let's Encrypt, proxy sin reescritura de URI, cabeceras sanitizadas, HTTP de la IP anterior → hostname HTTPS con 308. |
| Infra `compose/.env.example` | Parámetros HTTPS_HOSTNAME y LEGACY_HTTP_IP; no contiene secretos reales. |
| Infra `scripts/smoke-https.sh` | Health UP, React HTML, catálogo JSON, TLS confiable/hostname y redirecciones exactas por loopback con curl --resolve. |
| Infra `scripts/deploy-service.sh` | Mantiene SHA, flock, promoción atómica y rollback; agrega gate de mantenimiento, preflight HTTPS y mismo smoke después de deploy/rollback; diagnósticos de Caddy/Nginx. |
| Infra `tests/test_https.py` | Pruebas deterministas con Docker/curl simulados para éxitos, fallos TLS, falsos positivos y rollback. |
| Infra `tests/test_proxy_runtime.py` | Pruebas opcionales con Caddy/Nginx reales y backend eco local; sin AWS/ACME. |
| Infra `.github/workflows/ci.yml` | Agrega validación Caddy sin red, sintaxis Bash y tests de smoke/rollback; conserva validaciones Compose/Terraform. |
| Infra `.gitattributes`, `.gitignore` | LF en scripts/Caddyfile; excluye binarios/reportes locales de validación y caché Python. |
| Infra `terraform/main.tf`, `terraform/README.md` | Política TCP 443 y documentación coherente; no se ejecuta apply. |
| Infra `README.md`, `docs/deploy-aws-dev.md`, `docs/runbook.md` | Arquitectura, operación HTTPS y referencias actualizadas. |
| Infra `docs/https.md` | Comandos completos para sincronizar, pausar CD, guardar estado HTTP, migrar, validar, renovar, cambiar hostname y revertir infraestructura. |
| Infra `docs/https-validation.md` | Este informe. |
| Frontend `Front/frontend-atencion-ciudadana/nginx.conf` | Listener privado 8082 para Caddy, HTTP local 80 sin confiar en proxy headers; redirects relativos 308 con query; headers originales seguros; conserva 55m y adjuntos. |
| Frontend `.github/workflows/ci.yml` | Agrega nginx -t con imagen oficial sin red; no altera tests, cobertura, publicación por SHA ni CD. |

Backend: **sin cambios de archivos**. Compose configura las propiedades estándar de
Spring; el `.properties` existente ya admite M2_PUBLIC_BASE_URL para adjuntos.
`compose/compose.yml`: sin cambios; desarrollo local sigue en HTTP sin Caddy.

## Resultados

| Comprobación | Resultado |
| --- | --- |
| Compose local y deploy, usando exclusivamente archivos de ejemplo | `docker compose ... config --quiet` correcto en ambos. |
| Política de puertos, redes y volúmenes del Compose renderizado | Caddy 80/443; Frontend sin puertos; Backend loopback 8080; PostgreSQL sin publicación; mismos nombres de volúmenes de datos. |
| Caddyfile real con Caddy oficial 2.11.7 | Válido con binario Windows y con `caddy:2.11.7-alpine` Linux, en contenedor sin red. La validación no emitió certificados. |
| Nginx | `nginx -t` correcto con binario Windows y dentro de la imagen Frontend Linux recién construida; el tag nginx:1.28-alpine resolvió a Nginx 1.28.3. |
| Bash | `bash -n` correcto para ambos scripts con Git Bash y dentro de Linux; también se comprueba la sintaxis de los bloques Bash de la guía. |
| Pruebas de scripts | 10 pruebas correctas en Linux, incluyendo subcasos Backend/Frontend, 9 formas de falso positivo y recuperación con GHCR caído / imagen anterior en caché. En estas pruebas Docker/curl/flock son simulados; la integración Docker real se comprueba por separado. |
| Proxies reales en loopback | 5 pruebas correctas con Caddy 2.11.7 y Nginx 1.28.0: POST/PUT/PATCH multipart binario, URI/query, cabeceras falsas, HTTP local, React profundo, 308 de /api e IP, 413 del límite Nginx y descarga binaria con Content-Disposition. |
| Frontend lint | `npm run lint`: correcto. |
| Frontend tests/cobertura | 50 archivos, 1.281 tests correctos; líneas 98,96 %, ramas 96,08 %, funciones 98,86 %, statements 97,88 %. |
| Frontend build | `npm run build`: correcto. Advertencia de bundle >500 kB ya ajena al cambio HTTPS. |
| Builds Docker reales | Frontend con su Dockerfile/contexto y `VITE_API_BASE_URL=/api`: correcto. Backend actual: correcto con su Dockerfile, sin modificar fuentes; el empaquetado Docker omite tests como ya establecía ese Dockerfile. |
| Stack Linux integrado aislado | Compose real de deploy más overrides exclusivos del fixture: Caddy → Nginx → Spring Boot → PostgreSQL correcto, sin puertos publicados al host ni recursos existentes. Frontend/Caddy/PostgreSQL healthy y listener 8082 comprobado. |
| Flujos reales sobre TLS de prueba | Catálogo JSON, login, POST/PUT administrativo, creación multipart de ticket, URL HTTPS y descarga binaria autenticada de PNG correctos. Cabeceras originales de Spring comprobadas; no hubo pérdida de cuerpo. |
| Rollbacks Docker reales | Imagen Frontend incompatible devuelve 502; restauración recupera API/ticket/adjunto. Reversión a Compose HTTP anterior recupera HTTP y conserva el mismo PostgreSQL, volúmenes de datos/adjuntos/certificados y CA de prueba. |
| Runtime/simulador | Compose conserva tanto true como false y los demás bindings previos. El stack real se probó con SIMULATOR_ENABLED=true y lo conservó también tras revertir a HTTP. |
| Terraform | `terraform fmt -check`, `init -backend=false -input=false` y `validate`: correctos con 1.16.3; sin plan/apply ni acceso a recursos AWS. |
| git diff --check | Correcto en los tres repositorios. |
| Revisión de secretos/archivos runtime | Sin credenciales, certificados privados ni archivos runtime agregados por esta entrega. Las credenciales dummy de fixtures son sólo de prueba. |
| Compatibilidad CI/CD | actionlint en Linux pasó para ambos workflows. Contexto, Dockerfile, build arg, tags SHA y permisos de publicación son compatibles. Las versiones de Actions referenciadas existen; no se ejecutaron workflows, login ni push a GHCR desde esta sesión. |

La prueba nativa original usa backend eco/HTTP en loopback. La auditoría final Linux
agrega un proyecto Compose temporal con redes/volúmenes propios y **cero puertos al
host**, primero un backend eco y después Spring Boot/PostgreSQL reales. Sólo en ese
fixture se sustituye el issuer público por `tls internal` y se usa el hostname
`audit.example.test`. El cliente confía explícitamente en la CA local únicamente
para probar TLS; otro cliente con el trust store público la rechaza, como corresponde.
El Caddyfile de producción sigue usando Let's Encrypt y el smoke público no admite
certificados de prueba. **No se emitió ni verificó un certificado público de Lightsail.**
El límite 55 MiB se prueba por cabeceras contra Nginx sin transferir 55 MiB ficticios;
multipart y descarga de adjuntos sí transmiten bytes reales por Caddy y Nginx.

Los contenedores, redes y volúmenes temporales se eliminaron sólo después de verificar
su etiqueta de proyecto exclusiva. El inventario de recursos existentes quedó idéntico;
no se usó `down -v` ni prune. Las imágenes locales de auditoría quedan como caché,
sin publicación. Fixtures, logs y CA pública de prueba están en `.https-validation/`,
ignorados por Git; las claves de la CA estuvieron sólo en el volumen temporal eliminado.

## Incidencias resueltas

- `$scheme` en Nginx veía HTTP desde Caddy: el listener privado identifica el origen
  HTTPS; se descartan headers del cliente en Caddy y en la entrada HTTP local.
- `/api` podía redirigir a HTTP/perder query: redirección relativa 308 conserva
  origen, método y parámetros; también se desactivan redirects absolutos de directorios.
- Backend generaba URLs de adjuntos con localhost/HTTP si no se configuraba base:
  se deriva su base HTTPS del hostname en deploy.
- El smoke anterior aceptaba 3xx y no validaba TLS/contenido: ahora exige códigos,
  tipos y cuerpos correctos, redirecciones exactas y confianza TLS sin -k.
- Deploys concurrentes durante la migración: CD=false en ambos repos, drenaje de
  jobs, mismo flock y sentinel; nueva configuración rechaza deploys sin HTTPS sano.
- Primer intento Vitest falló por ENOENT en el temporal del sandbox; se repitió con
  TEMP/TMP dentro de node_modules y pasaron todos los tests. No se cambió la app.
- Git Bash requirió ejecución fuera del sandbox por restricciones MSYS; se validó
  sintaxis y pruebas con aprobación automática. Los mocks se anteponen al PATH tras
  iniciar Git Bash para no usar accidentalmente curl/Docker reales.
- Auditoría final: se eliminó la instrucción de forzar `SIMULATOR_ENABLED=false`
  en README, runbook y guía. Se conserva el valor runtime existente y ninguna otra
  variable previa se altera por HTTPS.
- Se corrigió la afirmación de ausencia de HSTS: Caddy no lo agrega, pero Spring
  Security sí lo devuelve en API HTTPS. La guía indica revertir por la IP HTTP
  anterior, porque el navegador puede seguir exigiendo TLS para el hostname.
- La reversión documentada comprueba primero la caché y usa `--pull never`, para
  recuperar las imágenes conocidas aunque GHCR no esté disponible. Los volúmenes
  y certificados no se borran; rollback de imagen y reversión de infraestructura
  siguen siendo procedimientos diferentes.
- Se reprodujo un fallo del script automático: el pull obligatorio de la imagen
  anterior impedía el rollback si GHCR caía, aunque la imagen estuviera en caché.
  Se corrigió para inspeccionar/reutilizar primero el SHA local y hacer pull sólo
  si falta. Los dos casos nuevos verifican éxito sin registry y fallo explícito
  cuando faltan tanto la imagen local como acceso a GHCR; images.env queda intacto.

## Pendientes externos y límites

El bloqueo Docker de la primera entrega quedó resuelto: en la auditoría final Docker
Desktop ya estaba funcionando. El sandbox denegaba el pipe; fuera del sandbox se
comprobó Engine 28.1.1 Linux/amd64, Compose 2.36.0 y Desktop 4.41.2. No se inició
el daemon ni se arrancaron contenedores preexistentes. Los builds e integración
Linux antes pendientes se ejecutaron correctamente.

No hubo cambios de código Backend; no se repitió su suite completa con PostgreSQL.
La configuración Spring por entorno quedó comprobada en Docker y debe reconfirmarse
en la instancia real durante el paso 6.
DNS A/AAAA/CAA efectivo, firewall, emisión y cadena pública Let's Encrypt, aceptación
externa y renovación real son comprobaciones pendientes en Lightsail. También quedan
pendientes carga/descarga funcional con tickets DEV, verificación de mounts reales,
CD real y ensayo de rollback con dos SHAs Frontend compatibles. Los tests locales
no reemplazan esas comprobaciones.

El push a GHCR y los jobs reales de GitHub quedan pendientes del commit/push futuro
del usuario y de los permisos ya configurados en los packages. Se verificaron los
metadatos oficiales de [checkout v7](https://raw.githubusercontent.com/actions/checkout/v7/action.yml),
[setup-node v6](https://raw.githubusercontent.com/actions/setup-node/v6/action.yml),
[login-action v4](https://raw.githubusercontent.com/docker/login-action/v4/action.yml)
y [build-push-action v7](https://raw.githubusercontent.com/docker/build-push-action/v7/action.yml).

## Selección exacta para commits futuros

No hay archivos staged en ninguno de los tres repositorios. No se ejecutó git add,
commit, push ni cambio de rama. Todos siguen en dev.

Frontend: incluir exclusivamente estos dos archivos de HTTPS:

```text
.github/workflows/ci.yml
Front/frontend-atencion-ciudadana/nginx.conf
```

Infra: estos 17 archivos corresponden a HTTPS, incluidos los nuevos sin seguimiento:

```text
.gitattributes
.github/workflows/ci.yml
.gitignore
README.md
caddy/Caddyfile
compose/.env.example
compose/compose.deploy.yml
docs/deploy-aws-dev.md
docs/https-validation.md
docs/https.md
docs/runbook.md
scripts/deploy-service.sh
scripts/smoke-https.sh
terraform/README.md
terraform/main.tf
tests/test_https.py
tests/test_proxy_runtime.py
```

Backend: ningún archivo para un commit de HTTPS.

Mantener separados, sin modificar ni incluir automáticamente:

```text
Frontend/Front/frontend-atencion-ciudadana/docs/quality.md
Frontend/H03-H04-FRONTEND-informe.md
Frontend/H05-FRONTEND-informe.md
Frontend/H18-H20-H28-informe.md
Backend/H05-BACKEND-informe.md
Backend/src/test/resources/events/v1.76/README.md
Backend/src/test/resources/events/v1.77/README.md
```

En el bloque anterior Frontend/ y Backend/ indican el repositorio correspondiente;
el resto de cada ruta es relativo a su raíz. No usar git add . en Frontend/Backend.
`.env`, `images.env`, `.https-validation/`, caches, binarios y artefactos de validación
no deben incluirse. Se verificaron ignore rules, diff y selección de archivos sin
detectar secretos preparados para commit. El tar se regenera desde la lista explícita
de diez archivos operativos y se verifica byte por byte contra la versión final.

La guía de activación exacta, reglas de firewall y reversión está en
[https.md](https.md). No declarar HTTPS operativo por un Caddy running/healthy ni
por una redirección HTTP. sslip.io puede fallar por DNS, abuso o límites de emisión;
nip.io es alternativa manual del mismo operador y no garantiza independencia.
