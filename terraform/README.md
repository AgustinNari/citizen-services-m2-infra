# Terraform AWS DEV

Esta carpeta contiene la base mínima para administrar posteriormente el entorno DEV en Amazon Lightsail: instancia, Static IP, asociación y puertos públicos. No incluye PROD ni otros servicios AWS.

La política pública contempla TCP 80 y 443, además de SSH restringido. No expone
Backend, PostgreSQL, la API de Caddy ni RabbitMQ. Para HTTPS en la instancia manual
existente usar [la migración](../docs/https.md); el cambio de este archivo no autoriza
un apply ni reemplaza la revisión/importación del state.

## Estructura

- `versions.tf`: versiones requeridas de Terraform y del provider AWS.
- `providers.tf`: configuración regional del provider.
- `variables.tf`: parámetros del entorno DEV.
- `main.tf`: recursos Lightsail.
- `outputs.tf`: nombre de instancia, Static IP y región.
- `terraform.tfvars.example`: valores públicos de referencia.

## Configuración

Copiar el ejemplo a un archivo local ignorado por Git:

```bash
cp terraform.tfvars.example dev.tfvars
```

Antes de ejecutar un plan, reemplazar el CIDR de documentación para SSH y confirmar que el blueprint, bundle y Availability Zone continúan disponibles en la región elegida. No guardar credenciales AWS en archivos `.tf` o `.tfvars`.

## Ciclo previsto

```bash
terraform init
terraform fmt
terraform validate
terraform plan -var-file=dev.tfvars
terraform apply -var-file=dev.tfvars
```

Actualmente **no se debe ejecutar `terraform apply`**. La instancia Lightsail ya existe manualmente; antes de administrarla con Terraform se deben ajustar las variables a los recursos reales, definir el manejo del state e importar esos recursos. Después corresponde revisar un `terraform plan` sin cambios destructivos.

El state es local. `.terraform/`, los archivos `terraform.tfstate*` y los `.tfvars` locales están ignorados y no deben versionarse. Cuando la infraestructura deje de ser experimental deberá evaluarse un backend remoto antes del uso colaborativo.
