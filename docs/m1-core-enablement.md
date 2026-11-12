# Preparación de M1 y Core (etapa 5)

Esta entrega prepara Infra. No activa integraciones, no despliega y no cambia AWS.
Conservar el `.env` privado existente, `images.env`, proyecto Compose, hostname HTTPS,
volúmenes `postgres_data` y `attachment_data`, y la clave/ID de tracking. No copiar
los ejemplos sobre archivos runtime ni ejecutar `down -v`.

## Configuración preparada

Los dos Compose transmiten explícitamente a Backend las variables de identidad,
M1 y Core. Declararlas sólo en `.env` no sería suficiente. El deploy deriva
`M2_PUBLIC_BASE_URL` y `M2_AUTH_BROWSER_ORIGIN` del **HTTPS_HOSTNAME vigente**;
no cambiarlo ni repetir la migración de dominio para habilitar M1.

Se conserva `SPRING_PROFILES_ACTIVE=dev`: actualmente MOCK y su inicializador
dependen de ese perfil. Ambos tienen condición adicional `IDENTITY_PROVIDER=MOCK`;
al seleccionar M1 no se ejecuta el inicializador MOCK. No borrar ni convertir
usuarios existentes. Los empleados M1 deben estar aprovisionados por UUID/tipo
con permisos locales; no copiar privilegios desde claims externos ni vincular por PII.

| Etapa deliberada | IDENTITY_PROVIDER | CORE_OUTBOX_ENABLED | CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS | CORE_OUTBOX_APPROVED_EVENT_IDS | CORE_INBOX_ENABLED |
|---|---|---|---|---|---|
| Preparación / configuración predeterminada | MOCK | false | false | vacía | false |
| M1 solamente | M1 | false | false | vacía | false |
| Publicación controlada | M1 | true | false | UUID v4 revisados | false |
| Recepción controlada | M1 | false | false | vacía | true |
| Operación continua, después de aceptación | M1 | true | true | vacía | habilitación separada |

En las etapas reales usar `SIMULATOR_ENABLED=false`. Manual y automática son
excluyentes. Inbox no depende de Outbox; habilitarlo puede consumir inmediatamente
el backlog de `q.atencion-ciudadana`, por lo que requiere coordinación con Core.

## Material privado y variables

Agregar o editar privadamente las entradas necesarias del `.env` existente, sin
imprimirlo, con permisos restrictivos (0600 y directorio de acceso limitado):

- `IDENTITY_PROVIDER`: conservar `MOCK` hasta la transición deliberada.
- `M1_BASE_URL=https://ciudadanos-m1-backend.onrender.com`.
- `APP_JWT_SECRET`: material que M1 utiliza para HS256, en Base64 y con al menos
  32 bytes decodificados. Obtenerlo por canal privado; no generar una clave distinta
  para hacer pasar el arranque ni confundirlo con la clave de tracking.
- `M1_CONNECT_TIMEOUT=5s`, `M1_READ_TIMEOUT=30s` por defecto; considerar el arranque
  en frío de M1 durante las pruebas. El proxy actual no requiere otro host público.
- `CORE_RABBIT_HOST`, `CORE_RABBIT_PORT=5671`, `CORE_RABBIT_VHOST=core`,
  `CORE_RABBIT_USERNAME` y `CORE_RABBIT_PASSWORD` proporcionados privadamente por Core.
  El host es DNS, sin `amqps://`, usuario o contraseña en una URL.
- Los flags de la tabla. La lista manual acepta UUID separados por coma; vacía no
  publica nada en modo manual. No seleccionar datos MOCK por el hecho de tener UUID.
- Los límites existentes `CORE_OUTBOX_MAX_ATTEMPTS`, `CORE_OUTBOX_RETRY_DELAY`,
  `CORE_OUTBOX_CONFIRM_TIMEOUT`, `CORE_OUTBOX_CONNECT_TIMEOUT`, `CORE_OUTBOX_SCAN_DELAY`,
  `CORE_INBOX_PREFETCH` y `CORE_INBOX_RECONNECT_DELAY` conservan los defaults de Backend.

Conservar también las credenciales PostgreSQL, las cuatro contraseñas MOCK y la
clave/ID de tracking existentes: Compose todavía las exige y permiten el rollback.
No poner secretos en GitHub Variables públicas, Docker build args, Frontend,
`images.env`, logs, artefactos CI ni ejemplos. No usar `source .env`, `set -x`,
`cat .env`, `docker inspect` completo o `docker compose config` sin filtrado.

## Validación sin activar servicios

El deploy script valida primero la configuración interpolada: bloquea M1 sin clave
válida/origen HTTPS, Core sin M1/credenciales o con destino distinto de 5671/core,
simulador con Core y mezcla de aprobación manual/automática. No conecta a M1/Core
ni Docker daemon para esta validación. Los errores no imprimen valores rechazados.

Desde la raíz **real** del repositorio desplegado, con los archivos privados vigentes:

```bash
set -o pipefail
docker compose --project-directory compose --env-file compose/.env \
  --env-file compose/images.env -f compose/compose.deploy.yml config --quiet
docker compose --project-directory compose --env-file compose/.env \
  --env-file compose/images.env -f compose/compose.deploy.yml config --format json \
  | python3 scripts/validate-integration-config.py
```

No guardar el JSON renderizado: contiene secretos. `config --quiet` comprueba
interpolación, no el estado remoto ni todas las validaciones de arranque de Backend.
El validador adicional no sustituye esas comprobaciones. Al desplegar Frontend,
el script contrasta además el proveedor configurado con el contenedor Backend
en ejecución y la etiqueta OCI de proveedor de la imagen descargada.

## Frontend: imagen M1 por digest (etapas 6 y 7)

El Dockerfile actual ya declara `ARG VITE_IDENTITY_PROVIDER=MOCK` y
`ARG VITE_API_BASE_URL=/api`. El Compose local permite pasar
`VITE_IDENTITY_PROVIDER=M1` al **build**, conservando `/api`. Para M1 local se necesita
un origen HTTPS funcional y `M2_AUTH_BROWSER_ORIGIN`/`M2_PUBLIC_BASE_URL` coherentes;
HTTP local no prueba una cookie `Secure`.

El Compose de deploy usa una imagen preconstruida. Poner `VITE_*` en el entorno del
contenedor Nginx o cambiar únicamente el `.env` del servidor **no recompila Vite**.
El workflow vigente de Frontend mantiene MOCK en los builds habituales y permite
`Actions → Frontend CI → Run workflow`, rama `dev` o `main`,
`identity_provider=M1`. Esa ejecución publica sin deploy una etiqueta
`m1-sha-<commit>-run-<run_id>-<attempt>` y registra en el resumen de publicación
la referencia completa `ghcr.io/santimussi/front-desarrollo-apps-2@sha256:...`.
Copiar esa referencia real; no inventar el digest ni sustituir el tag MOCK del mismo SHA.

Para un despliegue futuro aprobado, después de configurar y recrear Backend en
M1 con Core todavía apagado, desde el directorio Infra real:

```bash
# Sustituir el marcador por los 64 hexadecimales del digest publicado, sin los signos < >.
frontend_image='ghcr.io/santimussi/front-desarrollo-apps-2@sha256:<DIGEST_PUBLICADO>'
bash scripts/deploy-service.sh frontend "$frontend_image"
```

El selector nuevo acepta únicamente ese repositorio y un digest SHA-256 canónico
en minúsculas. Rechaza referencias arbitrarias y tags `latest`, `dev` o `m1-*`;
la etiqueta M1 sirve para localizar el digest, no como entrada del despliegue.
El formato `bash scripts/deploy-service.sh frontend SHA_COMPLETO` continúa vigente
para imágenes legacy; Backend conserva exclusivamente su selector SHA anterior.

El script descarga primero la imagen y comprueba su etiqueta
`ar.edu.uade.m2.identity-provider` emitida por el workflow de confianza, sin
deducir el modo del nombre. Los digests requieren metadata `M1` o `MOCK` y deben
coincidir con el Backend configurado **y en ejecución**. Editar `.env` sin recrear
Backend no alcanza. Esta comprobación lee sólo estado/proveedor del contenedor,
sin imprimir el resto de su entorno. No modifica flags, archivos privados ni Backend.

Compatibilidad histórica: un SHA anterior sin la etiqueta de proveedor puede
usarse sólo con Backend MOCK; su bundle no puede verificarse por esa metadata
ausente. Nunca se acepta una imagen sin etiqueta para M1 ni un digest sin etiqueta.
Conservar los SHA/digests anteriores para rollback; no reutilizar tags con otro modo.

No hay otro cambio indispensable de código Frontend ni de Nginx/Caddy para esta
preparación: el listener privado 8082 conserva HTTPS/origen, `/api`, métodos,
headers CSRF y cookies `Set-Cookie`. No publicar 8082, PostgreSQL ni puertos
entrantes de RabbitMQ. Los servicios remotos requieren salida HTTPS/AMQPS, no
un broker local ni declaraciones de topología en Infra.

## Secuencia posterior (no ejecutada en esta entrega)

1. Pausar CD en Backend y Frontend y esperar jobs en curso/pendientes. Aplicar
   primero los archivos de Infra aprobados, incluido el nuevo validador. Mantener
   los archivos privados y el proyecto Compose actual. El deploy de aplicaciones
   no actualiza el repositorio Infra remoto.
2. Registrar SHAs y respaldar privadamente `.env`, `images.env`, PostgreSQL y
   adjuntos con un procedimiento de restauración verificado. Preparar una imagen
   Backend que incluya V47–V50 y las etapas 4A/4B/4C; comprobar que el SHA publicado
   corresponde al código aprobado. No asumir que una imagen vieja entiende los flags.
3. Desplegar primero ese Backend en **MOCK y Core apagado**, con el mecanismo
   habitual. Flyway aplicará incrementalmente las migraciones pendientes al
   arrancar (requiere aprobación/ventana posterior). Verificar salud, usuarios,
   permisos, tickets, adjuntos y tracking; no reinicializar datos.
4. Tener lista la referencia por digest de Frontend M1 y el material privado M1.
   En una transición coordinada, seleccionar M1 en Backend con Core apagado y
   desplegar Frontend M1. Evitar mantener versiones con proveedores distintos;
   mantener CD pausado hasta aceptar el conjunto. Un push habitual de Frontend
   sigue publicando MOCK: su intento de deploy será rechazado mientras Backend sea M1.
   El deploy script habitual conserva lock compartido, preflight HTTPS y rollback
   de imagen. Un cambio de `.env` sólo se aplica al recrear el servicio afectado.
5. Probar login con ciudadano y empleado M1 autorizado, restauración al recargar,
   refresh automático, expiración y logout desde el origen HTTPS. Verificar
   `__Host-M2-Refresh` con `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`, sin
   `Domain`; CSRF/origen inválidos rechazados y `X-M2-CSRF` conservado. No registrar
   tokens ni credenciales. El smoke HTTPS actual sólo prueba salud/ruteo, no este flujo.
6. Coordinar con Core permisos, TLS/hostname, exchange existente `muni.inbox`, cola
   `q.atencion-ciudadana`, eventos suscritos y política vigente de reintentos/DLQ.
   No crear colas, exchanges, bindings o suscripciones desde esta preparación.
   Probar publicación manual de pocos eventos reales nuevos revisados, con consumo
   apagado. Confirmar broker/recepción y que el backlog local no se haya liberado.
7. Habilitar Inbox separadamente después de revisar su backlog con Core. Probar
   duplicados, transiciones y las variantes M1 efectivamente publicadas. M1 no
   garantiza todavía todas las emisiones; el login sigue complementando perfiles.
   Eventos aplicables con adjuntos externos harán NACK hasta acordar la descarga
   autenticada: comenzar sin adjuntos y coordinar revisión/reentrega de DLQ.
8. Después de aceptar los pasos anteriores, vaciar la lista manual y habilitar
   `CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS=true`, manteniendo M1, publicación activa
   y simulador apagado. Sólo eventos nuevos con procedencia persistida serán
   elegibles; los eventos anteriores y los tickets pre-V50 no se inscriben.
   Verificar creación identificada, enrutamiento anónimo y hechos SLA reales.

Usar SHAs aprobados para Backend y el digest aprobado para Frontend M1, como se
indica arriba. No desplegar ambos repositorios concurrentemente. No reactivar CD
de Frontend esperando que sus pushes habituales generen M1: siguen siendo MOCK.

## Rollback de imagen, configuración y base

El deploy script revierte **sólo la imagen** del servicio ante fallo de smoke;
no revierte `.env`, no deshace publicaciones ni migra la base hacia atrás.
Conservar una pareja de imágenes/configuración compatible y revisar también la
configuración si el fallo fue causado por nuevos flags/secretos.

El rollback admite como referencia anterior tanto `sha-<commit>` como el digest
del repositorio Frontend permitido. Usa la caché local antes de depender de GHCR y
conserva `images.env` anterior si el intento falla. Si un pull o la validación de
proveedor de la nueva imagen fallan, no recrea ningún servicio.

Si un despliegue entre versiones del mismo modo falla después de recrear,
restaura la imagen anterior y devuelve código 1. Si se cambió de MOCK a M1 (o al
revés), también restaura la imagen anterior, pero el Backend puede continuar en
el modo nuevo: devuelve código 2 y exige recuperación coordinada, sin ocultar la
incompatibilidad ni reiniciar Backend automáticamente. La reversión integral de
ambos proveedores no puede resolverse sólo cambiando la imagen Frontend.

Si falla una prueba funcional con un despliegue ya aceptado por el smoke, pausar
CD y coordinar la configuración/proveedor de Backend con la imagen anterior;
después seleccionar esa imagen con la misma interfaz (SHA legacy o digest).
No cambiar las claves ni la base para conseguir que funcione una imagen antigua.

Ante un problema de integración, deshabilitar publicación automática, Outbox e
Inbox y recrear únicamente Backend en una intervención coordinada. Los eventos
pendientes, reintentos e Inbox persistidos se conservan; al reactivar automática
pueden retomarse los eventos que ya eran elegibles. Los ya enviados no se retiran de Core.
Para volver a MOCK, coordinar también la imagen Frontend MOCK y conservar las
claves y usuarios M1 existentes; no convertirlos en usuarios MOCK.

V47–V50 son incrementales, pero no dar por compatible cualquier imagen antigua:
una anterior a V49 no aplica las restricciones externas M1 ni entiende los perfiles
parciales. No dejar integraciones activas con una imagen que no las soporte.
Conservar las migraciones aplicadas y probar el SHA de recuperación contra ese
esquema. Nunca editar `flyway_schema_history` ni deshacer columnas automáticamente.
Restaurar un backup de base es una operación distinta, con indisponibilidad,
posible pérdida de datos posteriores y conciliación de eventos ya entregados;
requiere una decisión operativa separada.
