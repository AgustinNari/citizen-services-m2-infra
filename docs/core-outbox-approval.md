# Aprobar eventos Core desde GitHub Actions

`Approve Core Outbox Events` permite a **cualquier colaborador con Write, Maintain o Admin**
de Infra aprobar de 1 a 50 UUID v4 distintos por ejecución desde **`main`**. Usa el Environment
**`core-outbox-approval`, sin revisores obligatorios y restringido exclusivamente a la rama
`main`**, junto con los permisos normales de GitHub. No tiene lista de usuarios que dependa
del propietario. Un colaborador con sólo Read/Triage necesita Write.
GitHub documenta [el requisito de Write y de publicar el workflow en la rama predeterminada](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

## Estado revisado y alcance

- Workspace revisado en `dev`: Infra `2666bcb`, con `origin/HEAD → main`. GitHub confirmó
  `main` como rama predeterminada. La URL antigua `AgustinNari/DA2-m2-infra` actualmente
  resuelve a `AgustinNari/citizen-services-m2-infra`; usar el repositorio Infra al que
  tengan acceso los compañeros. No se cambió el remote local.
- Revisión puntual final: la API de GitHub reportó `main` en `f1eccc6`, **sin protección**,
  y ningún ruleset. No se cambiaron esos permisos. Proteger `main` y configurar el Environment
  son requisitos previos a cargar la credencial y habilitar la herramienta.
- Backend ya admite una allowlist de UUID v4 (máximo 1000), procesa sólo `PENDING` con
  intentos disponibles y guarda `PUBLISHED` después del confirm de RabbitMQ, sin mensajes
  devueltos por unroutable. Su validador conserva la autoridad sobre payload e identidad M1.
- Se reutilizan Compose de deploy, `images.env`, smoke HTTPS, validador de integración,
  lock `$HOME/DA2/.deploy/m2-deploy.lock` y sentinel `m2-maintenance` existentes en Infra.
  Frontend usa Nginx con resolución dinámica del Backend, compatible con su recreación.
- No se modificaron Backend, Frontend ni Compose. **No se inspeccionó ni cambió Lightsail**;
  hay que verificar allí los prerrequisitos antes de habilitar el acceso.

## Configuración inicial, una sola vez

En Lightsail, usar la misma cuenta de los deployments (normalmente `ubuntu`) y el mismo
`HOME`. Necesita Docker/Compose, Python 3, Bash, curl, flock, timeout y CA roots; acceso
local a Docker y lectura/escritura del `.env`. No abrir PostgreSQL ni Backend al exterior.
SSH TCP 22 debe ser accesible desde el runner autorizado por su firewall. Los runners
GitHub hospedados tienen IP variables; si la política exige IP fija, usar un runner
con salida fija y ajustar `runs-on`, manteniendo las pruebas offline.

Verificar privadamente, sin imprimir configuración ni reemplazar archivos por ejemplos:

- Stack HTTPS sano, Backend con el procesador Outbox/confirmaciones ya desplegado.
- `.env` propietario `ubuntu`, archivo normal sin symlink, modo `0600`.
- `CORE_OUTBOX_ENABLED=true`, `CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS=false` tanto en `.env`
  como en el contenedor. `IDENTITY_PROVIDER=M1`, simulador apagado y credenciales AMQPS
  válidas, según `docs/m1-core-enablement.md` del checkout operativo.
- `CORE_OUTBOX_APPROVED_EVENT_IDS` es una asignación simple, opcionalmente vacía, sin
  referencias `${...}`, líneas múltiples ni claves duplicadas. Se permiten comillas simples/dobles.
- `images.env`, proyecto Compose, imagen/cache, montajes, redes, puertos y demás variables
  coinciden con el Backend desplegado. Si hay drift o cambió la versión de Compose,
  resolverlo con el procedimiento de deployment habitual antes de aprobar eventos.

### Instalar o actualizar en Lightsail

Tras revisar y publicar los archivos, copiar **sólo** `scripts/approve-core-outbox.sh`
y `scripts/core_outbox_approval.py` al directorio `scripts/` del checkout operativo:
`/home/ubuntu/DA2/Infra/DA2-m2-infra`. Mantener allí las versiones compatibles ya existentes
de `scripts/smoke-https.sh` y `scripts/validate-integration-config.py`. No hacer un `git pull`
de todo `main` sobre ese checkout operativo si le faltan los cambios de despliegue de `dev`.

Ejemplos manuales para Bash, **no ejecutados en esta entrega**. Desde el checkout local revisado,
generar una clave nueva fuera del repositorio y subir sólo los scripts y la pública usando
la conexión administrativa existente (no la nueva clave de aprobación):

```bash
umask 077
OUTBOX_KEY="$HOME/.ssh/m2-outbox-actions"
ssh-keygen -t ed25519 -a 100 -N '' -f "$OUTBOX_KEY" -C github-actions-infra-outbox
# No sobrescribir una clave existente; elegir otro nombre si ya existe.
SSH_HOST='<host-verificado>'
ADMIN_KEY='<ruta-a-la-clave-administrativa-existente>'
ssh -i "$ADMIN_KEY" -o StrictHostKeyChecking=yes "ubuntu@$SSH_HOST" \
  'umask 077; mkdir -p "$HOME/DA2/.deploy/outbox-reviewed"'
scp -i "$ADMIN_KEY" -o StrictHostKeyChecking=yes \
  scripts/approve-core-outbox.sh scripts/core_outbox_approval.py "$OUTBOX_KEY.pub" \
  "ubuntu@$SSH_HOST:/home/ubuntu/DA2/.deploy/outbox-reviewed/"
```

En una sesión administrativa de Lightsail, como `ubuntu`:

```bash
cd /home/ubuntu/DA2/Infra/DA2-m2-infra
mkdir -p "$HOME/DA2/.deploy"
chmod 700 "$HOME/DA2/.deploy"
(
  flock -w 600 9 || exit 1
  install -m 700 "$HOME/DA2/.deploy/outbox-reviewed/approve-core-outbox.sh" scripts/approve-core-outbox.sh
  install -m 600 "$HOME/DA2/.deploy/outbox-reviewed/core_outbox_approval.py" scripts/core_outbox_approval.py
  bash -n scripts/approve-core-outbox.sh
  chmod 600 compose/.env
) 9>"$HOME/DA2/.deploy/m2-deploy.lock"
```

Estos pasos se ejecutan manualmente por un administrador **una vez por instalación/actualización**,
no por aprobación. Conservar el directorio y propietario actuales. El workflow no sube ni
ejecuta código enviado desde el runner: invoca únicamente el script instalado.

Agregar la nueva pública al `~ubuntu/.ssh/authorized_keys` con esta restricción
(nunca agregar la misma clave también sin restricción):

```text
restrict,command="/bin/bash /home/ubuntu/DA2/Infra/DA2-m2-infra/scripts/approve-core-outbox.sh" ssh-ed25519 <PUBLIC_KEY> github-actions-infra-outbox
```

Comandos en esa sesión administrativa, una sola vez (ajustar el nombre de la pública si cambió):

```bash
umask 077
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
{ printf 'restrict,command="/bin/bash /home/ubuntu/DA2/Infra/DA2-m2-infra/scripts/approve-core-outbox.sh" '; \
  cat "$HOME/DA2/.deploy/outbox-reviewed/m2-outbox-actions.pub"; } >> "$HOME/.ssh/authorized_keys"
```

Usar modos `0700` para `.ssh` y `0600` para `authorized_keys`. La clave sólo acepta el comando
`approve-core-outbox-v1`, sin argumentos; no permite shell interactiva, PTY ni forwarding.
Mantener protegidos la cuenta de deployment y los scripts: el acceso general de esa cuenta
a Docker sigue siendo privilegiado. Obtener y verificar la host key fuera de Actions mediante
un canal confiable; no confiar en un `ssh-keyscan` hecho automáticamente durante cada ejecución.

### GitHub: protección de main y Environment

Primero publicar selectivamente los archivos del apartado siguiente **sin cargar aún la clave**
y esperar que pase `Core Outbox Approval CI`. Luego, como administrador:

1. **Settings → Branches → Branch protection rule → `main`**: exigir PR y al menos una
   aprobación de otro colaborador, descartar aprobaciones obsoletas y exigir aprobación del
   último push. Incluir administradores, impedir force push y borrado. La revisión corresponde
   a cambios de código, **no a cada ejecución de aprobación de eventos**.
2. Exigir el check **`Core Outbox approval offline tests`** y rama actualizada. La CI no tiene
   filtro de paths, para que el check no quede pendiente en PR de otros archivos.
3. **Settings → Environments → `core-outbox-approval`**: sin required reviewers, sin wait timer.
   En **Deployment branches and tags → Selected branches and tags**, agregar **solamente
   Ref type `Branch`, patrón exacto `main`**. No usar “All branches”, “Protected branches”,
   `*`, `dev`, tags ni refs de pull requests. El `if` del YAML es una defensa adicional;
   la política del Environment es la barrera contra workflows alterados en otras ramas.

Los [secretos de Environment y sus reglas de rama](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
son aplicados por GitHub antes de entregar el secreto al job. No confiar sólo en una condición
del workflow. Los administradores del repositorio siguen siendo parte del perímetro de confianza.

Configurar **dentro de ese Environment**, no a nivel Repository:

| Tipo | Nombre | Valor |
| --- | --- | --- |
| Environment Secret | `OUTBOX_SSH_PRIVATE_KEY` | Privada dedicada, sin passphrase, correspondiente a la pública restringida |
| Variable | `LIGHTSAIL_HOST` | Hostname o IPv4 verificado; SSH puerto 22 |
| Variable | `LIGHTSAIL_USER` | `ubuntu`, la misma cuenta/HOME de deployments |
| Variable | `LIGHTSAIL_KNOWN_HOSTS` | Línea known_hosts verificada para ese host exacto |
| Variable | `OUTBOX_APPROVALS_ENABLED` | Inicialmente `false`; `true` al completar la instalación |

No requiere PAT, credenciales PostgreSQL en GitHub ni `LIGHTSAIL_DEPLOY_PATH`: el destino
lo fija el comando SSH restringido. `.env`, claves de cifrado y credenciales Core quedan
en el servidor. `GITHUB_TOKEN` sólo usa `contents: read`. **Eliminar cualquier copia Repository
u Organization Secret accesible desde Infra de esta clave**; si se había cargado como Repository
Secret, rotarla y revocar la pública anterior. Revisar también que Infra no conserve otra clave
SSH general como Repository/Organization Secret: permitiría eludir esta separación.

Alternativa concreta con `gh`, ejecutada manualmente por el administrador tras publicar
(no reemplazar una protección existente más estricta con estos valores de ejemplo):

```bash
REPO='AgustinNari/citizen-services-m2-infra'
APPROVAL_ENV='core-outbox-approval'
gh api --method PUT "repos/$REPO/branches/main/protection" --input - >/dev/null <<'JSON'
{"required_status_checks":{"strict":true,"contexts":["Core Outbox approval offline tests"]},"enforce_admins":true,"required_pull_request_reviews":{"dismiss_stale_reviews":true,"required_approving_review_count":1,"require_last_push_approval":true},"restrictions":null,"allow_force_pushes":false,"allow_deletions":false}
JSON
gh api --method PUT "repos/$REPO/environments/$APPROVAL_ENV" --input - >/dev/null <<'JSON'
{"wait_timer":0,"reviewers":[],"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
JSON
# Crear esta regla una vez; si el Environment ya existe, quitar antes sus otras reglas en Settings.
gh api --method POST "repos/$REPO/environments/$APPROVAL_ENV/deployment-branch-policies" \
  -f name=main -f type=branch >/dev/null
# Comprobar que hay exactamente UNA regla: Branch main; y ningún revisor/timer.
gh api "repos/$REPO/environments/$APPROVAL_ENV/deployment-branch-policies" \
  --jq '.branch_policies | map({name,type})'
gh api "repos/$REPO/environments/$APPROVAL_ENV" \
  --jq '{deployment_branch_policy,protection_rules}'
gh variable set OUTBOX_APPROVALS_ENABLED --repo "$REPO" --env "$APPROVAL_ENV" --body false
gh variable set LIGHTSAIL_HOST --repo "$REPO" --env "$APPROVAL_ENV" --body "$SSH_HOST"
gh variable set LIGHTSAIL_USER --repo "$REPO" --env "$APPROVAL_ENV" --body ubuntu
gh variable set LIGHTSAIL_KNOWN_HOSTS --repo "$REPO" --env "$APPROVAL_ENV" < /ruta/known_hosts-verificado
gh secret set OUTBOX_SSH_PRIVATE_KEY --repo "$REPO" --env "$APPROVAL_ENV" < "$OUTBOX_KEY"
# Si existía la copia Repository Secret, eliminarla (y rotar la clave como se indicó).
# gh secret delete OUTBOX_SSH_PRIVATE_KEY --repo "$REPO"
```

El archivo known_hosts debe contener la host key previamente verificada para el host exacto,
no una captura sin verificar. Estos comandos configuran el acceso **una sola vez**; no dar
permisos de administración ni la clave privada a los colaboradores para ejecutar aprobaciones.

## Publicar en main y habilitar colaboradores

GitHub sólo registra `workflow_dispatch` cuando el workflow existe en **`main`**. Dejarlo
únicamente en `dev` no lo habilita en Actions. Publicar un cambio acotado con estos archivos:

```text
.github/workflows/approve-core-outbox.yml
.github/workflows/core-outbox-approval-ci.yml
scripts/approve-core-outbox.sh
scripts/core_outbox_approval.py
scripts/run_core_outbox_approval.py
scripts/validate-integration-config.py
tests/test_core_outbox_approval.py
tests/test_core_outbox_shell.py
docs/core-outbox-approval.md
```

El validador de integración **ya existe en `dev`** y es una dependencia necesaria para las
pruebas autónomas en `main`; incluir ese archivo sin traer todos los cambios de `dev`.
No hace falta publicar Compose, scripts de deployment, Terraform ni el README completo de `dev`
en `main`. Los scripts operativos/Compose ya deben existir en Lightsail según el apartado anterior.
La CI dedicada valida esta herramienta con fixtures, independientemente de la CI general de Infra.
Se puede agregar a mano un enlace a este documento en el README actual de `main`.

En `dev` publicar los ocho archivos nuevos del listado (todos excepto el validador, que ya está
versionado), más **`README.md` y `docs/runbook.md`** con sus enlaces/instrucciones actualizados:
**10 archivos**. En `main`, publicar **exactamente los 9 del listado**, conservando los otros
archivos de esa rama. No copiar el README ni el runbook completos de `dev` a `main`.

Una vez publicado: habilitar GitHub Actions y permitir la acción oficial `actions/checkout`
en las políticas del repositorio/organización. Conceder Write a los compañeros y poner
`OUTBOX_APPROVALS_ENABLED=true` **en el Environment, después de proteger main y comprobar
su regla Branch main**. No agregar revisores al Environment. Las políticas
de organización que bloqueen Actions o secretos deben ser resueltas una sola vez por su administrador.
No hay que elevar los permisos de `GITHUB_TOKEN` a escritura.

## Uso diario

1. Infra → **Actions → Approve Core Outbox Events → Run workflow**.
2. Elegir **`main`**. En `dev` funciona la CI offline; la aprobación real se omite.
3. Pegar los UUID de **eventos**, no los UUID de tickets. Ejemplo ficticio:

   ```text
   11111111-1111-4111-8111-111111111111,22222222-2222-4222-9222-222222222222
   ```

   También acepta saltos de línea en el valor recibido. Si la UI los aplana, usar comas.
   Se normalizan mayúsculas y se eliminan duplicados; se rechazan UUID no v4, variante
   no RFC, tokens vacíos entre comas y más de 50 distintos. No se ofrece modo automático general.
4. Ejecutar y leer **Summary** y el paso de aprobación. El registro de Actions identifica
   al actor, momento y ejecución; la salida sólo contiene UUID, estados y códigos acotados.

El servidor espera hasta 600 s por el lock compartido; durante toda la operación impide
deploys simultáneos de Backend/Frontend y respeta mantenimiento. Rechaza todo el lote antes
de modificar `.env` si algún evento falta, está `FAILED`, agotó intentos o su tipo no es soportado.
Los ya `PUBLISHED` se aceptan sin volver a publicarlos. Se conserva la unión de aprobaciones
`PENDING` en archivo/contenedor y los UUID nuevos, hasta 1000; se eliminan del archivo las
aprobaciones ya terminales o inexistentes. No se consultan payloads ni datos de ciudadanos.

El candidato conserva los demás bytes de `.env`, se valida con Compose y se reemplaza
atómicamente con modo `0600`. Sólo si faltan aprobaciones pendientes en el contenedor se usa
`up -d --no-deps --no-build --pull never backend`. Se comprueba la imagen local y el hash de la
configuración completa del Backend; no se aplica drift ajeno. No se toca `images.env`, PostgreSQL,
Frontend, Caddy ni volúmenes. Podría haber una interrupción breve de Backend durante su recreación.

## Interpretar resultados, reintentar y detener

| Resultado | Significado / acción |
| --- | --- |
| `PUBLISHED` | Todos los UUID solicitados están persistidos como publicados y Backend/HTTPS están UP. Confirmación del broker, no del procesamiento por consumidores. |
| `PENDING` | Finalizó la espera de hasta 180 s y quedan pendientes; ejecución con error, sin afirmar publicación. La aprobación sigue vigente y el worker continúa sus reintentos. |
| `FAILED` | El worker rechazó el evento o agotó intentos. Revisar de forma privada con el responsable de Backend; esta herramienta no restablece estados ni intentos. |
| `BATCH_REJECTED` | Revisar la tabla por UUID. No se modificó el archivo ni se recreó Backend. Separar los eventos válidos en una nueva ejecución. |
| `BACKEND_DRIFT` / `CONFIGURATION_REJECTED` | Resolver configuración, imagen, permisos o prerequisitos mediante el procedimiento operativo habitual; no forzar el workflow. |
| `APPLY_FAILED` / `HEALTH_FAILED` | Puede haber aprobaciones persistidas y publicaciones parciales. Recuperar la disponibilidad y repetir los mismos UUID. |
| `DATABASE_UNAVAILABLE` / `UNKNOWN` / `UNVERIFIED` | No se pudo observar un resultado confiable; no equivale a fallo o éxito de publicación. Revisar conexión/servidor y repetir los mismos UUID. |

La validación final de esquema, identidad y privacidad la hace **Backend antes de enviar**.
No se duplica ese código en Infra: un evento `PENDING` técnicamente aprobable puede terminar
`FAILED` por validación. La herramienta nunca hace `UPDATE`, reintenta estados terminales ni
deduce éxito de un `docker up`, logs o conectividad RabbitMQ. Hay comprobaciones de health
local y smoke HTTPS antes/después. El servidor está acotado a 900 s tras obtener el lock.

Si falló la recreación, se retiene la allowlist escrita y **no hay rollback automático de
aprobaciones**: algunos eventos podrían haber sido confirmados. Una repetición reconcilia
archivo/contenedor sin perder pendientes; no reenvía eventos ya `PUBLISHED`. Si Backend quedó
detenido, recuperar su arranque con el runbook antes de reintentar. Un confirm cuyo commit SQL
se perdió puede causar reentrega (semántica at least once del Outbox); consumidores deben
deduplicar por `eventId`. No se promete entrega exactamente una vez.

Para impedir **nuevas ejecuciones**, cambiar `OUTBOX_APPROVALS_ENABLED=false`, deshabilitar
el workflow o revocar la pública dedicada. En el servidor se puede crear
`$HOME/DA2/.deploy/m2-outbox-disabled` para bloquear nuevas aprobaciones sin bloquear deployments.
Esto no revoca una ejecución activa ni aprobaciones ya aplicadas; cancelar Actions tampoco.

Para detener publicación pendiente con urgencia, un operador debe tomar el **mismo lock**,
poner mantenimiento, detener sólo Backend, editar privadamente la allowlist quitando los
UUID que se quieran revocar, conservar `ENABLED=true` y `AUTO_APPROVE_NEW_EVENTS=false`,
recrear sólo Backend con las imágenes actuales y verificar health/HTTPS antes de quitar
mantenimiento. Dejar la allowlist vacía detiene toda nueva publicación manual. No cambiar
estados SQL ni intentar deshacer eventos ya confirmados: requieren compensación funcional.
Para retirar la herramienta, además quitar la clave SSH y deshabilitar/eliminar sus workflows.

## Primera prueba segura tras la habilitación manual

Desde la máquina administrativa, comprobar que la clave dedicada **no** abre una shell
(esperar `Outbox command rejected.` y exit 64; no debe mostrar el usuario remoto):

```bash
ssh -T -i "$OUTBOX_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes "ubuntu@$SSH_HOST" id
```

El administrador habilita la variable una sola vez tras verificar la protección anterior:

```bash
gh variable set OUTBOX_APPROVALS_ENABLED --repo "$REPO" --env "$APPROVAL_ENV" --body true
```

Un compañero con Write, desde su propia sesión GitHub/`gh`, puede hacer estas pruebas
sin recibir la privada ni tener permisos para modificar el Environment:

```bash
REPO='AgustinNari/citizen-services-m2-infra'
# Input inválido: falla ANTES de SSH; no modifica el servidor.
gh workflow run approve-core-outbox.yml --repo "$REPO" --ref main -f event_ids=not-a-uuid
# UUID v4 recién generado, que no exista en Outbox; nunca usar aquí un PENDING real.
MISSING_EVENT_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
gh workflow run approve-core-outbox.yml --repo "$REPO" --ref main -f "event_ids=$MISSING_EVENT_ID"
gh run list --repo "$REPO" --workflow approve-core-outbox.yml --branch main --limit 5
RUN_ID='<id-de-la-ejecucion>'
gh run view "$RUN_ID" --repo "$REPO" --log-failed
```

El cambio de variable lo realiza el administrador; el compañero sólo necesita el comando
`workflow run` o Actions → Run workflow. Para el UUID inexistente, esperar **`BATCH_REJECTED`**,
estado **`NOT_FOUND`**, `env_written=false` y `recreated=false`. El job rojo es esperado:
comprueba SSH restringido, lectura de DB y salida segura sin aprobar ni publicar nada.
Verificar en Settings que `dev` y tags no están autorizados; ejecutar desde `dev` debe omitir
el job de aprobación. Esa omisión comprueba el guard del YAML, no sustituye la revisión de
la política del Environment aplicada por GitHub.

Para comprobar el circuito completo, un compañero puede aprobar **un solo evento real ya
revisado y autorizado para publicar**, y repetir después el mismo UUID. Exigir `PUBLISHED`
y Backend/HTTPS `UP`; la repetición debe seguir `PUBLISHED` sin recrear Backend ni reenviarlo.
Esta última prueba sí publica si el evento estaba pendiente: hacerla sólo cuando corresponda.
Las pruebas negativas no demuestran disponibilidad del broker ni confirman publicación.

## Pruebas reproducibles sin servicios reales

```bash
bash -n scripts/approve-core-outbox.sh
python3 -m unittest discover -s tests -p 'test_core_outbox*.py' -v
# En dev, también ejecutar toda la regresión de Infra:
python3 -m unittest discover -s tests -v
```

Las pruebas cubren entradas hostiles/límites, lotes inválidos, preservación de `.env`/imágenes,
allowlists divergentes, drift, idempotencia, fallos parciales, estados sin confirm, health,
salida sin secretos, SSH restringido y bloqueo compartido real en Linux. Un proceso completo
usa Docker/curl/smoke ficticios y reemplazo atómico real; nunca se conecta a Lightsail ni al broker.
No prueban credenciales reales, disponibilidad pública ni políticas de GitHub: esas verificaciones
quedan para la habilitación manual.

La revisión final vuelve a ejecutar sólo las pruebas de esta herramienta, el ensayo del paquete
selectivo sobre el `main` verificado, `actionlint`, parseo de las políticas YAML y sintaxis Bash.
No configura ni prueba las políticas reales del Environment ni la credencial SSH. Su instalación
y comprobación manual son necesarias antes de declararlo habilitado en producción.

Resultado de la revisión puntual: **READY_FOR_COMMIT**, con **44/44 pruebas relevantes en Linux
y 44/44 sobre el paquete selectivo de main**, sin cambios en los otros archivos de esa rama.
También pasaron `actionlint`, políticas YAML y sintaxis de los comandos documentados (sin
ejecutarlos). Se corrigieron el alcance de la credencial por rama, el check obligatorio con
filtros de paths, el rechazo de SSH sin comando y el límite interno para reconciliar dos
allowlists grandes. El límite público sigue siendo 50 UUID y el efectivo de Backend, 1000.
Quedan pendientes la configuración y las pruebas manuales reales descritas arriba.
