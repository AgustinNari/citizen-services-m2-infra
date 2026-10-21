# Deploy AWS DEV

Instancia existente `m2-dev`, Ubuntu 24.04, región `sa-east-1`, Static IPv4
`54.232.233.237`. Docker Engine y Compose plugin son requisitos; las aplicaciones
se descargan de GHCR por SHA y Caddy usa una versión oficial explícita.

**Para activar HTTPS sobre esta instancia existente, seguir [https.md](https.md)
en orden.** Incluye sincronización de archivos locales sin commit, pausa y
reactivación de CD, backup privado, conservación de proyecto/volúmenes, imagen
Frontend compatible, validaciones, apertura TCP 443, activación y reversión a HTTP.
No usar `git archive dev` para transportar esta entrega sin commit: omitiría cambios.

El servidor conserva Infra en `/home/ubuntu/DA2/Infra/DA2-m2-infra`, con
`compose/.env` e `images.env` privados existentes. No copiarlos desde ejemplos.
No recrear la instancia, Static IP, PostgreSQL ni volúmenes durante la migración.

Firewall final: TCP 22 restringido a administración, TCP 80/443 públicos. Backend
permanece en `127.0.0.1:8080`; no publicar PostgreSQL, 8082, API Caddy 2019 ni
puertos entrantes de RabbitMQ. No se usa UDP 443. La configuración Terraform
refleja HTTPS pero no se aplica al recurso manual sin importar/revisar el state.

La operación diaria, GHCR privado, Secrets/Variables y rollback de imágenes están
en [runbook.md](runbook.md). Validaciones locales y pendientes externos están en
[https-validation.md](https-validation.md). HTTPS sólo se declara operativo cuando
la instancia supera el smoke TLS sin excepciones y las comprobaciones externas.
