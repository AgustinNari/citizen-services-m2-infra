#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${QA_TRUSTSTORE_PASSWORD:?Falta QA_TRUSTSTORE_PASSWORD}"
if [[ -f /certificates/trust.p12 ]]; then
  keytool -list -keystore /certificates/trust.p12 -storepass:env QA_TRUSTSTORE_PASSWORD >/dev/null 2>&1 || {
    echo 'La contraseña del truststore QA cambió. Restaurala o recreá únicamente los volúmenes descartables de QA.' >&2; exit 1;
  }
  openssl x509 -checkend 86400 -noout -in /certificates/server.crt >/dev/null
  chown 100:101 /certificates/server.key
  chmod 400 /certificates/server.key
  exit 0
fi
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj '/CN=M2 disposable QA CA' \
  -keyout "$work/ca.key" -out "$work/ca.crt" >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -subj '/CN=rabbitmq' \
  -keyout "$work/server.key" -out "$work/server.csr" >/dev/null 2>&1
printf '%s\n' 'subjectAltName=DNS:rabbitmq' 'extendedKeyUsage=serverAuth' > "$work/extensions"
openssl x509 -req -days 30 -in "$work/server.csr" -CA "$work/ca.crt" -CAkey "$work/ca.key" \
  -CAcreateserial -extfile "$work/extensions" -out "$work/server.crt" >/dev/null 2>&1
keytool -importcert -noprompt -alias qa-root -file "$work/ca.crt" -keystore "$work/trust.p12" \
  -storetype PKCS12 -storepass:env QA_TRUSTSTORE_PASSWORD >/dev/null 2>&1
cp "$work/ca.crt" "$work/server.crt" "$work/server.key" /certificates/
chmod 755 /certificates
chmod 644 /certificates/ca.crt /certificates/server.crt
# The pinned RabbitMQ Alpine image uses rabbitmq UID 100 / GID 101.
chown 100:101 /certificates/server.key
chmod 400 /certificates/server.key
# Write the completion marker last; it contains a public CA, no private key.
cp "$work/trust.p12" /certificates/trust.p12
chmod 644 /certificates/trust.p12
echo 'Certificados locales QA preparados; no se exportaron claves ni contraseñas.'
