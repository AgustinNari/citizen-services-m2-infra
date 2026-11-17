# Infraestructura M2

Este repositorio contiene la configuración local y operativa de AWS Lightsail DEV. El Compose local construye desde los repositorios vecinos; el Compose de deploy consume exclusivamente imágenes privadas e inmutables de GHCR. Los repositorios deben conservar esta estructura relativa para el desarrollo local:

```text
DA2/
├── Backend/
│   └── Backend-DAMII/
├── Frontend/
│   └── front-desarrollo-apps-2/
└── Infra/
    └── DA2-m2-infra/
```

`compose/compose.yml` levanta Frontend (React + Nginx), Backend y PostgreSQL para desarrollo. El repositorio Backend también posee un Compose propio para desarrollar únicamente Backend + PostgreSQL sin clonar Infra. Son alternativas válidas y no conviene ejecutarlas simultáneamente con sus puertos predeterminados, ya que pueden colisionar, especialmente en el puerto `8080`.

## Requisitos

- Docker
- Docker Compose plugin

## Arranque aislado de QA y G1

Para probar el sistema completo sin M1/Core reales, usar **`compose/qa`**. Requiere
Git, Docker con contenedores Linux y Compose **2.17 o superior** con BuildKit.
Java, Maven, Node, certificados y broker se preparan en contenedores. La primera
compilación necesita Internet para descargar imágenes y dependencias; los servicios
Backend, broker, base y Core simulado se ejecutan luego en una red Docker `internal`,
sin salida a Internet. Sólo Nginx/Frontend se conecta además a una red de borde para
publicar los puertos de loopback en Docker Desktop; también proxifica salud y controles QA.
Prever al menos 8 GB de RAM para Docker y espacio para imágenes y compilaciones.

Clonar los tres repositorios con estos nombres y estructura, desde una carpeta vacía:

```bash
git clone --branch dev https://github.com/JuanmaGuida/Backend-DAMII.git Backend/Backend-DAMII
git clone --branch dev https://github.com/SantiMussi/front-desarrollo-apps-2.git Frontend/front-desarrollo-apps-2
git clone --branch main https://github.com/AgustinNari/DA2-m2-infra.git Infra/DA2-m2-infra
cd Infra/DA2-m2-infra/compose/qa
```

Los nombres de carpetas son parte del procedimiento: los contextos de build son
relativos, sin rutas particulares de Windows. Usar las revisiones acordadas de los
tres repositorios para la entrega. `main` es la rama estable de Infra; los cambios
en `dev` deben revisarse e integrarse antes de publicarlos.

Preparar `.env` desde `.env.example`: completar `POSTGRES_PASSWORD`, las cuatro
`MOCK_*_PASSWORD`, `QA_RABBIT_PASSWORD`, `QA_TRUSTSTORE_PASSWORD` y una clave de
seguimiento Base64 de **32 bytes aleatorios**. Mantener DB/usuario/ID de clave con
los valores locales del ejemplo, o completar valores propios. No copiar el `.env`
de Lightsail. No dejar passwords vacías ni reutilizar contraseñas históricas.

Alternativamente, este paso de configuración funciona en Bash y PowerShell y crea
`.env` con todos los secretos aleatorios directamente en el archivo, sin mostrarlos:

```bash
docker run --rm --network none --mount "type=bind,source=$PWD,target=/qa" --workdir /qa --entrypoint bash eclipse-temurin:21-jdk-noble@sha256:06c1f157dacf6b68198bd9a4186dd4c34ef24e65d7bbbe2a0cf8e62571ad38c9 /qa/prepare-env.sh
```

El preparador se niega a sobrescribir un `.env` existente. Se pueden editar los
puertos `QA_*_PORT` antes del arranque. Las contraseñas no se imprimen; evitar
publicar `docker compose config`, `docker inspect` o volcados del entorno, que
pueden contenerlas. `.env` está ignorado por Git y excluido del contexto de build.

Con la configuración completa, **un único comando** construye y levanta todo:

```bash
docker compose up --build --wait --wait-timeout 600
```

Compose rechaza variables obligatorias vacías indicando su nombre. El comando
espera PostgreSQL, RabbitMQ con TLS, Core simulado, Backend con migraciones y
Frontend saludables. `--wait-timeout` limita la espera de salud, no la compilación:
medir desde la ejecución del comando hasta su terminación para comprobar los 15 minutos.

Accesos predeterminados, publicados únicamente en loopback:

| Servicio | Dirección | Uso |
|---|---|---|
| Frontend | `http://localhost:18081` | Portal y API mediante `/api` |
| Salud Backend vía Nginx | `http://127.0.0.1:18080/actuator/health` | Debe responder HTTP 200 y `UP` |
| Core simulado | `http://127.0.0.1:18090/health` | Debe responder HTTP 200 y `UP` |

Ingresar en el Frontend con `citizen@example.test`, `agent@example.test`,
`area.responsible@example.test` o `module.admin@example.test`, y la contraseña de
`MOCK_CITIZEN_PASSWORD`, `MOCK_AGENT_PASSWORD`, `MOCK_AREA_RESPONSIBLE_PASSWORD` o
`MOCK_ADMIN_PASSWORD`, respectivamente. Son identidades locales existentes; no
son cuentas M1. El catálogo y los roles se inicializan con las migraciones y el perfil `dev`.

Comprobación funcional repetible, desde `compose/qa` y sin Java instalado en el host:

```bash
docker compose exec -T core-mock java --class-path "/core/BOOT-INF/lib/*" /core/VerifyG1.java
```

Debe finalizar con JSON `status: PASS`: comprueba Frontend/API, login MOCK ciudadano,
agente y administrador. Prepara mediante la API administrativa un tipo sintético
`QA_G1_EXTERNAL_ANONYMOUS`, sin cambiar los tipos existentes (el catálogo inicial
no habilita anónimos), y su formulario de una pregunta. Comprueba creación anónima,
revisión, derivación externa y publicación Outbox por
AMQPS, recepción/validación en Core simulado, envío de `updateTicketStatus/STARTED`
por AMQPS y aplicación del Inbox hasta `IN_PROGRESS`. También rechaza un sobre con
campo no declarado y comprueba deduplicación del Core simulado. Crea un ticket
sintético nuevo en cada ejecución, reutiliza ese tipo QA y sólo imprime IDs y resultados.

El recorrido es anónimo porque el publicador real de M2 exige identidad M1 verificada
para eventos identificados: no se debilita esa regla ni se transforma un usuario MOCK
en M1. La publicación automática local requiere exclusivamente los perfiles `dev`
y `qa-isolated`, `CORE_OUTBOX_LOCAL_QA=true`, identidad MOCK, host `rabbitmq`, broker
habilitado, simulador apagado y lista manual vacía. Esa excepción sólo admite eventos
anónimos sin `citizenId`; no marca su procedencia como M1. Sus guardas se prueban al
construir el Core simulado. Sin ese opt-in se conserva la política existente.
El Frontend y Backend usan siempre `MOCK`, M1 apunta a `m1.invalid`, IA y
simulador interno están apagados y RabbitMQ apunta exclusivamente a `rabbitmq:5671`.
Estas selecciones no se heredan del shell ni del `.env` productivo.

El inicializador genera una CA descartable, certificado de servidor con SAN
`rabbitmq` y truststore Java. Backend y Core simulado verifican certificado y hostname;
no usan trust-all. La CA privada se elimina al terminar el inicializador. Las claves
runtime, certificados, datos y adjuntos permanecen en volúmenes del proyecto **`m2-qa`**,
separados de desarrollo y Lightsail. No hay puertos de PostgreSQL ni RabbitMQ publicados.

El Core simulado reutiliza los clientes AMQP/Jackson y los schemas `v1.78` de la
misma imagen Backend: valida `ticketCreated`, `ticketUpdated` y `updateTicketStatus`,
persiste recibos sin payload personal y confirma los mensajes después de persistir.
Sus rutas `/qa/events` y `/qa/publish` son controles exclusivos del laboratorio, no
endpoints del Core real. Usa una cola local `q.qa.rejected` para diagnóstico; no
implementa el catálogo, permisos, cadena completa de reintentos ni API DLQ del Core.
G1 y este recorrido no acreditan por sí solos todas las pruebas de resiliencia.

### INT-04: caída y recuperación de RabbitMQ local

Con el mismo entorno QA saludable (`docker compose up -d --build --wait`) y Python 3
en el host, ejecutar desde `compose/qa`:

```bash
python demo-int04.py --tickets 3
```

La demo verifica primero el contexto Docker local, los contenedores `m2-qa` y el
aislamiento. Detiene sólo su RabbitMQ durante al menos 120 segundos, crea N tickets
anónimos mediante la API, los revisa y deriva al área simulada. Reutiliza el tipo
sintético G1: para este recorrido corresponde **un evento `ROUTED` por ticket**.
Comprueba tickets persistidos y Outbox `PENDING`, reinicia el broker y espera la
publicación con los mismos `eventId` y payloads, sin reiniciar Backend, PostgreSQL
ni Core simulado. Después envía N actualizaciones contractuales `STARTED` y verifica
Inbox, actividad, mensaje y estado `IN_PROGRESS`.

Finalmente reenvía físicamente dos copias de cada evento en ambos sentidos por
AMQPS: exige un solo recibo/efecto en Core y un solo Inbox, actividad y mensaje por
actualización, colas drenadas y ningún rechazo adicional. La entrega del transporte
puede repetirse; la comprobación exige efectos idempotentes. El CLI de replay vive
sólo en el Core simulado y admite eventos previamente registrados en ese laboratorio.

Debe terminar con `INT-04 PASS` y conteos JSON. Cada ejecución usa UUID nuevos y
comprueba exclusivamente su lote; conserva los datos para inspección y restaura
el broker en caso de error o interrupción normal. No ejecutar dos demos simultáneas.
`--tickets` admite 1..100; `--outage-seconds` cambia la duración y
`--timeout-seconds` el plazo de recuperación (240s por defecto). Una caída extensa
puede requerir aumentar este plazo: Outbox conserva el backoff exponencial, con
tope de una hora. Las fallas de transporte/confirmación siguen pendientes hasta
recuperarse; payloads inválidos y rechazos que agotan los intentos siguen en `FAILED`.
Los errores identificados de autenticación, validación TLS, protocolo o topología
usan `PUBLICATION_ERROR` y conservan el máximo de intentos; no se confunden con una
caída de transporte. El Core simulado también pausa cinco segundos antes de
reencolar por fallas de almacenamiento, evitando un ciclo de reentregas inmediato.
No modificar las variables de publicación ni usar este script en otro Compose.

Para ver la salud de todos los servicios: `docker compose ps -a` (el inicializador
`certificates` debe terminar con código 0; los cinco servicios restantes deben estar
`healthy`). Para detener sin borrar datos: `docker compose down`.
Para volver a levantarlos conservando datos y claves: `docker compose up --wait --wait-timeout 600`.
Para reiniciar un servicio sin reconstruir: `docker compose restart <servicio>` y
comprobar su salud con `docker compose ps`. Para volver a probar desde
cero, revisar que se está en `compose/qa` y ejecutar `docker compose down --volumes`:
**borra únicamente los datos descartables de ese proyecto QA**. No cambiar passwords
de PostgreSQL/RabbitMQ/truststore con volúmenes existentes; restaurarlas o recrear ese
entorno descartable. Los certificados vencen en 30 días. Nunca usar estos archivos
como configuración de `compose.deploy.yml` ni copiarlos a Lightsail.

## Configuración local

Desde el repositorio Infra:

```bash
cd compose
cp .env.example .env
```

Editá `.env` y completá las contraseñas vacías con valores privados locales. Generá también la clave persistente y su ID según [la guía existente](docs/tracking-code-encryption.md). No reutilices claves sintéticas de tests. El archivo `.env` real está ignorado por Git y no debe versionarse. El simulador queda apagado por defecto; un desarrollador puede habilitarlo explícitamente con `SIMULATOR_ENABLED=true`.

Variables por entorno (sin versionar valores reales):

```dotenv
# LOCAL
POSTGRES_DB=m2
POSTGRES_USER=m2_app
POSTGRES_PASSWORD=<PASSWORD_LOCAL>
FRONTEND_PORT=8081
MOCK_CITIZEN_PASSWORD=<PASSWORD_LOCAL_CITIZEN>
MOCK_AGENT_PASSWORD=<PASSWORD_LOCAL_AGENT>
MOCK_AREA_RESPONSIBLE_PASSWORD=<PASSWORD_LOCAL_AREA>
MOCK_ADMIN_PASSWORD=<PASSWORD_LOCAL_ADMIN>
SIMULATOR_ENABLED=false # usar true sólo cuando el desarrollador lo necesite
ATTACHMENT_STORAGE_ROOT=/var/lib/m2/attachments
```

En Lightsail se conserva el `.env` runtime existente, incluidas passwords, almacenamiento y el valor actual de `SIMULATOR_ENABLED` (sin imponer `true` ni `false` durante HTTPS). Se agregan `HTTPS_HOSTNAME=54-232-233-237.sslip.io` y `LEGACY_HTTP_IP=54.232.233.237`. `COMPOSE_PROJECT_NAME` debe conservar el nombre efectivo del despliegue existente. `FRONTEND_PORT` sólo afecta desarrollo local. Las versiones desplegadas viven por separado en `images.env`; ese archivo no contiene secretos y tampoco se versiona. No reemplazar archivos runtime existentes con ejemplos.

## Comandos habituales

Ejecutar desde `Infra/DA2-m2-infra/compose`:

```bash
# Levantar
docker compose up --build --wait --wait-timeout 600

# Reconstruir y levantar
docker compose up -d --build

# Ver estado
docker compose ps

# Ver logs
docker compose logs -f

# Detener sin borrar datos
docker compose down
```

No ejecutes `docker compose down -v` salvo que quieras eliminar deliberadamente los volúmenes y los datos de PostgreSQL y adjuntos.

## Puertos locales

- Frontend: `http://localhost:8081` por defecto; se puede cambiar con `FRONTEND_PORT`.
- Backend: `http://127.0.0.1:8080`, accesible sólo desde la misma máquina.
- PostgreSQL: `5432` únicamente dentro de la red Docker, sin publicación al host.
- Adjuntos: volumen persistente `attachment_data`, montado en `/var/lib/m2/attachments`.

En Lightsail se usa `compose.deploy.yml`, autocontenido y sin ninguna clave `build:`. Las aplicaciones usan imágenes GHCR por SHA y Caddy usa la imagen oficial `2.11.7-alpine`. Caddy publica TCP `80/443`, termina TLS público y renueva certificados con datos persistentes. Frontend no publica puertos; Backend conserva `127.0.0.1:8080`, y PostgreSQL no publica puertos. El HTTP de la IP anterior redirige explícitamente al hostname HTTPS.

```text
Browser HTTPS
   |
   v
Caddy :443 (HTTP :80 redirige a HTTPS)
   |
   v
Frontend / Nginx :8082 privado
   |-- /    -> React
   `-- /api
          |
          v
       Backend :8080
          |
          v
       PostgreSQL :5432
```

El alta de AWS está documentada en [`docs/deploy-aws-dev.md`](docs/deploy-aws-dev.md). La operación diaria, configuración de GHCR/CD, smoke tests y rollback están en [`docs/runbook.md`](docs/runbook.md).

**Para migrar desde HTTP, seguir primero [docs/https.md](docs/https.md)**: apagar CD,
guardar el proyecto/volúmenes vigentes y Compose anterior, publicar Frontend compatible,
liberar 80 y recién iniciar Caddy. Incluye comandos completos de sincronización,
firewall, mantenimiento, cambio de dominio y reversión a HTTP. El script nuevo bloquea
deploys mientras exista `/home/ubuntu/DA2/.deploy/m2-maintenance` y exige HTTPS sano.
Los resultados y límites de validación local están en [docs/https-validation.md](docs/https-validation.md).

## Clave de seguimiento recuperable

Ambos Compose requieren `TRACKING_CODE_ENCRYPTION_KEY_BASE64` y `TRACKING_CODE_ENCRYPTION_KEY_ID`
para Backend. Preparar una clave privada persistente antes de publicar/desplegar la nueva imagen:
[procedimiento y orden seguro de CD](docs/tracking-code-encryption.md). Los ejemplos no contienen
una clave por defecto. Se conserva el HTTPS vigente y no se configura Lightsail desde esta entrega.

## Aprobaciones manuales del Outbox

Para aprobar UUID del Outbox desde GitHub Actions con permisos Write de Infra, consultar
[configuración inicial, publicación en main y operación](docs/core-outbox-approval.md).
La herramienta usa aprobación explícita, SSH restringido y el lock compartido de deployments.

## Alcance de la verificación

El arranque comprueba servicios, migraciones y proxy. `VerifyG1.java` verifica el
recorrido de integración aislado descrito arriba. Las demás historias y pantallas
deben contrastarse con el plan de QA de la versión acordada; este procedimiento
no sustituye esa validación funcional ni la homologación con M1/Core reales.

## Desarrollo básico y validación de release

El comando local anterior espera PostgreSQL sano, Backend con HTTP 200 en
`/actuator/health` y luego Frontend sano. Los usuarios MOCK son
`citizen@example.test`, `agent@example.test`, `area.responsible@example.test` y
`module.admin@example.test`; usar las contraseñas privadas configuradas en las
cuatro variables `MOCK_*_PASSWORD`. El modo local predeterminado es MOCK, con
simulador y Core deshabilitados. No usar credenciales M1 reales para pruebas locales.

Este Compose básico no incluye broker. Para G1 completo usar el entorno aislado
`compose/qa` documentado arriba y registrar su tiempo con las revisiones de entrega.
Lightsail utiliza exclusivamente `compose.deploy.yml`; no aplicar allí los pasos de QA.

Los tags `v*` ejecutan Infra CI sin despliegue ni cambios AWS. Para QA se registra
ref/SHA y resumen de secretos. Sonar, ZAP y homologación real con Core siguen pendientes.

Desde la raíz: `python .github/scripts/check-secrets.py` (Python 3.11+ y Git).
Usa Gitleaks 8.30.1 temporal, con SHA-256 verificado, sobre historial completo de
las refs locales y archivos versionables actuales (incluidos untracked no ignorados).
No descarga refs; requiere un clon completo. `--gitleaks <ruta>` permite usar esa
versión instalada; `--output <archivo-fuera-del-repo.json>` conserva sólo metadata.
Los `.env` privados ignorados no se escanean ni se deben agregar a Git. Nunca subir
reportes crudos. Sólo se exceptúa la fixture pública bytes 0..31 en rutas revisadas;
no hay exclusiones por directorios completos ni por commits. El resultado limpio
no prueba que credenciales históricas de otros repositorios no se hayan reutilizado.
