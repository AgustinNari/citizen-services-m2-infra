# Preparación de la clave persistente de seguimiento

Esta entrega prepara configuración; no instala secretos ni despliega en Lightsail.
Conservar el HTTPS vigente de Dynu, Caddy, Nginx, hostname, certificados, volúmenes y `/api`.
No repetir la migración de dominio. No reemplazar `.env` por un ejemplo.

## Orden antes de publicar Backend

1. En GitHub, poner `CD_ENABLED=false` en Backend (y mantener suspendido cualquier despliegue
   concurrente durante la ventana). Esperar a que terminen los jobs de deploy ya iniciados o
   pendientes; cambiar la variable no los cancela. No hacer todavía push del Backend nuevo.
2. Revisar/aprobar y publicar primero los cambios de configuración de Infra. Llevar al directorio
   efectivo de despliegue el `compose/compose.deploy.yml` actualizado. Conservar `.env`,
   `images.env`, `COMPOSE_PROJECT_NAME`, imágenes actuales y demás archivos runtime.
   El deploy de Backend sólo cambia una imagen; no actualiza el Compose remoto.
3. En la instancia, como operador, generar **una sola vez** la clave dentro del `.env` existente
   mediante el procedimiento siguiente. Guardar una copia privada de recuperación fuera del host.
4. Validar Compose sin imprimirlo. Con la configuración preparada, publicar el Backend nuevo,
   esperar CI y desplegar su SHA mediante el script habitual.
5. Validar alta/recuperación, autorización y recreación con la misma clave antes de reactivar CD.
   Frontend no se modifica ni se habilita en esta entrega.

La configuración es obligatoria también para las imágenes antiguas cuando se usa el Compose
actualizado. Preparar `.env` y Compose antes de cualquier recreación, incluido un rollback.

## Generación privada en Lightsail (pasos manuales, no ejecutados)

Entrar en el directorio real indicado por `LIGHTSAIL_DEPLOY_PATH` del workflow. Confirmar que
es el despliegue existente y que `compose/.env` e `images.env` son los archivos vigentes.
No usar `source`, `cat .env`, `docker inspect` completo ni `docker compose config` sin `--quiet`.

Desde ese directorio, este bloque genera y guarda la clave sin imprimirla. Se niega a sustituir
una clave o ID ya existentes, incluso vacíos: si hay una preparación anterior, revisarla privadamente
en lugar de regenerar. Conserva las líneas previas y sus valores; crea un respaldo privado local
del `.env` anterior y una copia del nuevo en el mismo directorio protegido.

```bash
umask 077
python3 - <<'PY'
import base64, os, pathlib, re, secrets, tempfile, time
p = pathlib.Path('compose/.env')
if not p.is_file():
    raise SystemExit('No existe el .env del despliegue; detenerse')
old = p.read_bytes()
names = ('TRACKING_CODE_ENCRYPTION_KEY_BASE64', 'TRACKING_CODE_ENCRYPTION_KEY_ID')
text = old.decode('utf-8')
if any(re.search(r'^\s*(?:export\s+)?' + name + r'\s*=', text, re.M) for name in names):
    raise SystemExit('Ya existe configuración de tracking; conservarla y revisar sin imprimir')
state = pathlib.Path.home() / 'DA2/.deploy/tracking-key-backups'
state.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(state, 0o700)
stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + secrets.token_hex(4)
key = base64.b64encode(secrets.token_bytes(32)).decode('ascii')
new = old + (b'' if old.endswith(b'\n') else b'\n')
new += ('TRACKING_CODE_ENCRYPTION_KEY_BASE64=' + key + '\n'
        'TRACKING_CODE_ENCRYPTION_KEY_ID=dev-v1\n').encode('ascii')
for suffix, contents in (('before', old), ('with-key', new)):
    with (state / (stamp + '.' + suffix + '.env')).open('xb') as f:
        os.chmod(f.name, 0o600)
        f.write(contents)
fd, tmp = tempfile.mkstemp(prefix='.env.tracking.', dir=p.parent)
try:
    with os.fdopen(fd, 'wb') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(new)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
print('Configuración guardada. Respaldo privado en ~/DA2/.deploy/tracking-key-backups; no imprimirlo.')
PY

docker compose --project-directory compose --env-file compose/.env \
  --env-file compose/images.env -f compose/compose.deploy.yml config --quiet
```

Transferir el respaldo `with-key.env` mediante un canal privado al almacenamiento de recuperación
del equipo, con acceso restringido. Es un respaldo del `.env` completo: nunca Git, chat, issues
ni artefactos CI. No considerar suficiente una copia que sólo exista en la misma instancia.
No borrar ni reemplazar la clave en reinicios, despliegues o cambios de hostname.

La validación `config --quiet` comprueba interpolación y presencia; Backend comprueba formato
Base64, longitud de 32 bytes e ID al arrancar. No tiene fallback hash-only para nuevas altas.

## Despliegue posterior y aceptación

Con CI satisfactorio y la ventana preparada, usar el mecanismo habitual:

```bash
bash scripts/deploy-service.sh backend SHA_COMPLETO_APROBADO
bash scripts/smoke-https.sh atencion-ciudadana.ddnsfree.com 54.232.233.237
```

No pegar tokens ni códigos en comandos/historial. Mediante un cliente autenticado que no registre
bodies: crear un ticket identificado, conservar su respuesta temporalmente y consultar
`GET /api/me/tickets/{ticketId}/tracking-code`. Debe devolver exactamente el mismo código,
con `Cache-Control: no-store`. Confirmar 401 sin sesión, 404 de ajeno/anónimo/inexistente,
409 para históricos y seguimiento público por hash intacto. Revisar respuestas generales y eventos
sin imprimir credenciales. El health/smoke HTTPS no sustituye estas pruebas funcionales.

Recrear sólo Backend con la misma configuración y verificar otra vez ese ticket:

```bash
docker compose --project-directory compose --env-file compose/.env \
  --env-file compose/images.env -f compose/compose.deploy.yml up -d --no-deps --force-recreate backend
bash scripts/smoke-https.sh atencion-ciudadana.ddnsfree.com 54.232.233.237
```

No recrear PostgreSQL, borrar volúmenes ni usar `down -v`. Registrar SHAs y aceptación sin secretos.
Reactivar CD únicamente al terminar; el deploy script ya usa el mismo lock para ambos servicios.
El Frontend posterior se publica después de validar este Backend y su persistencia de clave.

## Fallos y rollback

La clave ausente/vacía impide renderizar Compose; una inválida impide arrancar Backend. No utilizar
valores de CI para hacer pasar el arranque. Los errores no deben incluir el valor rechazado.
Usar logs acotados; no habilitar TRACE de bindings SQL ni logging de cuerpos. Restringir también
logs PostgreSQL y respaldos de base; son información interna sensible.

El rollback de imagen conserva V46, datos, clave y keyId. No restaurar automáticamente el `.env`
`before` después de haber creado tickets cifrados. Una imagen antigua puede crear filas all-null;
serán no recuperables y seguirán funcionando por hash. Perder/cambiar clave no cambia el hash,
pero impide recuperar los códigos anteriores. No hay rotación automática ni keyring en esta versión.

## Desarrollo y CI

Los dos Compose de Infra y el Compose de Backend pasan ambas variables sólo a Backend.
En local, preparar una clave propia en `.env` ignorado; los ejemplos dejan valores vacíos.
CI usa únicamente una clave sintética conocida y comprueba que falte cualquiera de las variables
para rechazar la configuración. Ninguna clave se usa en el build de las imágenes o en Frontend.
