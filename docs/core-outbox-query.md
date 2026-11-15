# Consultar metadatos del Outbox desde Actions

`Query Core Outbox Events` es independiente de la aprobación: busca por **un `ticket_id`**
(puede tener muchos eventos) o por **un `event_id`**. No aprueba, reintenta ni publica eventos.
Se ejecuta desde `main` por colaboradores Write, usando el Environment y la clave restringida
ya existentes. No se crean Secrets, Environments, credenciales de DB ni claves nuevas.

## Uso

1. Infra → Actions → **Query Core Outbox Events → Run workflow**, rama **`main`**.
2. `lookup_by`: elegir `ticket_id` o `event_id`. `lookup_id`: pegar un único UUID completo,
   sin comas ni listas. Se normalizan mayúsculas y espacios externos. `event_id` requiere v4;
   `ticket_id` admite el formato UUID canónico del contrato Backend.
3. Abrir **Summary**: tabla de metadatos y bloque CSV para copiar. No se adjuntan artefactos
   ni se imprimen filas en los logs. GitHub conserva el Summary según la retención del repositorio.

Ejemplos manuales de una línea con `gh` (reemplazar los UUID ficticios por los buscados):

```bash
gh workflow run query-core-outbox.yml --repo AgustinNari/citizen-services-m2-infra --ref main -f lookup_by=ticket_id -f lookup_id=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
gh workflow run query-core-outbox.yml --repo AgustinNari/citizen-services-m2-infra --ref main -f lookup_by=event_id -f lookup_id=11111111-1111-4111-8111-111111111111
```

Se muestran `event_id`, `ticket_id`, `correlation_id`, `created_at`, `published_at`,
`published_at_argentina`, `status` y `area`. Se agregan **`event_type` y `update_type`** porque
permiten distinguir la creación de un ticket de sus derivaciones, progresos, cambios de estado,
etc.; ambos existen como columnas del Outbox en Backend. No se devuelve ningún otro dato del payload.

Orden: **`created_at DESC NULLS LAST, event_id DESC`**, determinista. Como máximo **100 eventos**;
se lee uno adicional para indicar claramente si el resultado está truncado. No se presenta
ese resultado parcial como un historial completo. `created_at` y `published_at` se muestran
en UTC con offset; `published_at_argentina` usa exactamente
`published_at AT TIME ZONE 'America/Argentina/Buenos_Aires'`, hora local **sin offset**, como
en la consulta solicitada por Core. Se aplica la zona horaria de PostgreSQL, no una resta fija.
`NULL` en tabla/CSV significa dato ausente, no un valor textual almacenado.

| Resultado | Interpretación |
| --- | --- |
| `OK` | Consulta correcta con eventos; los `PENDING`, `PUBLISHED` y `FAILED` se muestran tal como están en DB. |
| `NO_RESULTS` | Consulta correcta sin eventos para ese UUID; job exitoso, sin filas inventadas. |
| `INVALID_INPUT` | Formato/modo inválido; no se consulta la DB. El cliente valida antes de SSH y el servidor vuelve a validar. |
| `INVALID_RESULT` | Datos/respuesta fuera del contrato o demasiado grandes; se omiten íntegramente, sin imprimir valores crudos. |
| `QUERY_FAILED` / `UNVERIFIED` | No se obtuvo una respuesta verificable. Revisar configuración, scripts o DB; no equivale a ausencia de eventos. |

La consulta es una observación de la DB: no habla con RabbitMQ ni certifica el procesamiento
de consumidores. Consultar o repetir una búsqueda nunca cambia estados, intentos o aprobaciones.

## Seguridad y contratos revisados

Se revisaron `OutboxEvent`, `OutboxStatus`, `TicketUpdatedType`, migraciones V21/V48 y los
schemas v1.78 del Backend. Event IDs son UUID v4; correlation ID es un UUID opcional; el área
es un código `M1`…`M9` opcional. Los timestamps del Outbox son `timestamptz` y `published_at`
puede ser nulo. Los resultados validan estos tipos tanto en Lightsail como en el runner;
si una fila contiene texto libre, datos privados o columnas inesperadas, no se publica la respuesta.
No se seleccionan ciudadanos, mensajes, adjuntos, contraseñas, tokens ni el payload completo.

El comando SSH forzado conserva su **mismo nombre/ruta** en `authorized_keys`. Su dispatcher
acepta sólo `approve-core-outbox-v1` y `query-core-outbox-v1`, sin argumentos. No usa `eval`,
no ejecuta comandos suministrados por el runner ni acepta SFTP/SCP o shells. JSON entra por stdin;
el modo SQL pertenece a una lista fija y el único literal variable es un UUID validado. Se usa
la misma verificación estricta de host key, sin forwarding ni distribución de la privada al equipo.

La consulta comparte el lock `$HOME/DA2/.deploy/m2-deploy.lock` durante toda la operación
(espera máxima 600 s) y respeta `m2-maintenance` y `m2-outbox-disabled`. El proceso de consulta
está limitado a 60 s, el statement SQL a 5 s y la espera por locks SQL a 2 s. Usa `psql -X`,
`ON_ERROR_STOP`, sesión con `default_transaction_read_only=on` y transacción **`BEGIN READ ONLY`**.
Sólo ejecuta la proyección fija requerida. No renderiza configuración, recrea servicios, edita
`.env`/`images.env`, crea índices ni cambia permisos. No necesita Backend o RabbitMQ disponibles
para consultar, aunque sí PostgreSQL y el Compose operativo.

La transacción de [sólo lectura de PostgreSQL](https://www.postgresql.org/docs/17/sql-set-transaction.html)
rechaza modificaciones de tablas. No se promete una instantánea compartida entre ejecuciones:
el worker puede cambiar legítimamente el estado de un evento entre dos consultas.

## Habilitación mínima pendiente — no ejecutada en esta entrega

### GitHub

Se reutilizan el **Environment `core-outbox-approval`**, su regla **Branch `main`** sin revisores
obligatorios y los valores existentes: `LIGHTSAIL_HOST`, `LIGHTSAIL_USER`, `LIGHTSAIL_KNOWN_HOSTS`,
`OUTBOX_SSH_PRIVATE_KEY` y `OUTBOX_APPROVALS_ENABLED=true`. Este último flag habilita ambos clientes
manuales; `false` también impide la consulta. Los sentinels del servidor bloquean ambas operaciones.
No mover la clave a Repository Secrets ni ampliar el Environment a `dev`, tags o comodines.

`main` ya está protegida (confirmado por lectura de la API). Publicar en `dev` mediante su flujo
habitual y preparar **un PR acotado, basado en `main`**, únicamente con estos ocho archivos:

```text
.github/workflows/query-core-outbox.yml              nuevo
scripts/core_outbox_query.py                        nuevo
scripts/run_core_outbox_query.py                    nuevo
tests/test_core_outbox_query.py                     nuevo
docs/core-outbox-query.md                           nuevo
scripts/approve-core-outbox.sh                      actualizar
scripts/run_core_outbox_approval.py                 actualizar
docs/core-outbox-approval.md                        actualizar
```

El mismo conjunto corresponde a esta funcionalidad en `dev`. No fusionar un PR completo de
`dev` hacia `main`, no omitir revisiones/checks ni hacer force push. Mantener el check existente
**`Core Outbox approval offline tests`**: ya descubre los nuevos tests, sin modificar su workflow.
Los scripts/dependencias y pruebas de aprobación ya publicados en `main` siguen siendo necesarios;
no llevar los cambios ajenos de CI, Compose, README, Backend o Frontend.

### Lightsail

Actualizar **únicamente dos archivos** en el checkout operativo, como la misma cuenta/HOME del
deployment (normalmente `ubuntu`): nuevo `scripts/core_outbox_query.py` y actualizado
`scripts/approve-core-outbox.sh`. Conservar el `scripts/core_outbox_approval.py` ya instalado.
Los runners usan `run_core_outbox_query.py` y `run_core_outbox_approval.py` desde GitHub:
no hay que instalarlos en el servidor. Ubuntu/runner necesitan Python 3 con tzdata; PostgreSQL
ya tiene su catálogo de zonas horarias.

Después de copiar los dos archivos revisados mediante la conexión administrativa existente a
`$HOME/DA2/.deploy/outbox-reviewed/`, ejecutar en Lightsail, desde el checkout operativo:

```bash
cd /home/ubuntu/DA2/Infra/DA2-m2-infra
flock -w 600 "$HOME/DA2/.deploy/m2-deploy.lock" bash -c 'set -e; install -m 600 "$HOME/DA2/.deploy/outbox-reviewed/core_outbox_query.py" scripts/core_outbox_query.py; install -m 700 "$HOME/DA2/.deploy/outbox-reviewed/approve-core-outbox.sh" scripts/approve-core-outbox.sh; bash -n scripts/approve-core-outbox.sh'
```

Se instala primero el módulo nuevo y al final el dispatcher, bajo el lock. La línea existente
`restrict,command="/bin/bash /home/ubuntu/DA2/Infra/DA2-m2-infra/scripts/approve-core-outbox.sh" ...`
de `authorized_keys` **no se cambia**. No generar otra clave, abrir puertos de DB, levantar
servicios ni hacer un `git pull main` completo en el checkout del servidor.
Comprobar que la restricción vigente realmente apunta a esa ruta: en esta entrega sólo se
revisaron código y documentación de SSH, sin inspeccionar ni modificar el servidor.

### Primera comprobación manual

Desde Actions en `main`, consultar un `event_id` UUID v4 inexistente: esperar `NO_RESULTS`,
sin escrituras ni recreaciones. Consultar después un ticket conocido para ver sus múltiples
eventos y un evento de esa lista para comprobar la correspondencia y copiar el CSV para Core.
No usar el workflow de aprobación para probar esta consulta. La aprobación existente conserva
su comando y contrato originales; la regresión automática se ejecuta junto a los tests nuevos.

Si hay problemas, deshabilitar sólo `Query Core Outbox Events` en Actions; para bloquear ambas
operaciones usar el flag/sentinel existente. Para retirar la extensión basta reinstalar el
dispatcher anterior (bajo el mismo lock); el módulo de consulta queda inaccesible por la clave.
No se necesitan cambios de DB, rollback de estados ni rotación de credenciales para revertir código.

## Pruebas reproducibles

Offline, incluyendo aprobación, transporte y dispatcher:

```bash
python3 -m unittest discover -s tests -p 'test_core_outbox*.py' -v
```

Con PostgreSQL 17 **local y ficticio**, opt-in (no usa Lightsail). Requiere la imagen `postgres:17`
ya disponible: el test usa `--pull=never`, sin red, puertos publicados ni mounts del host,
almacenamiento temporal y limpieza al finalizar. Valida SQL real, nulos, zona horaria, límites,
checksum de registros intacto y rechazo efectivo de `UPDATE` en modo read-only:

```bash
OUTBOX_QUERY_POSTGRES_TESTS=1 python3 -m unittest discover -s tests -p 'test_core_outbox*.py' -v
```

`OUTBOX_QUERY_DOCKER` permite indicar la ruta de la CLI Docker local. Es sólo una variable de
pruebas, no configuración del workflow/servidor. La CI normal no inicia este contenedor: las
tres pruebas PostgreSQL se omiten si no se habilita el opt-in; en esta entrega sí se ejecutan localmente.

Validación de esta entrega: **67/67 pruebas**, incluidas las 44 de aprobación y las tres con
PostgreSQL real ficticio; también **67/67 sobre una copia temporal de `main` con sólo los ocho
archivos de esta extensión**. Pasaron `actionlint`, políticas YAML, sintaxis Bash y verificación
de que el resto de `main` no cambia. Estado: **READY_FOR_COMMIT**; no se hicieron commits, pushes,
PR, cambios de permisos/credenciales ni instalaciones en Lightsail.
