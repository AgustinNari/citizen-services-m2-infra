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

## Configuración local

Desde el repositorio Infra:

```bash
cd compose
cp .env.example .env
```

Editá `.env` y reemplazá todos los placeholders de passwords por valores locales. El archivo `.env` real está ignorado por Git y no debe versionarse. El simulador queda apagado por defecto; un desarrollador puede habilitarlo explícitamente con `SIMULATOR_ENABLED=true`.

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

Ejecutar desde `Infra/compose`:

```bash
# Levantar
docker compose up -d

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

## Alcance funcional pendiente

Este bloque garantiza build, arranque, migraciones y proxy, pero no completa los endpoints
funcionales que el Frontend espera y el Backend aún no implementa. Esas divergencias no
impiden desplegar el stack; algunas pantallas pueden recibir `401`, `404` o `405` hasta que
se resuelvan en tareas funcionales posteriores.
