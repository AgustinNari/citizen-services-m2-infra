#!/usr/bin/env bash
# Read-only end-to-end checks from the server. No public-IP hairpin needed.
set -Eeuo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <https-hostname> [legacy-http-ip]" >&2
  exit 64
fi
hostname="$1"
legacy_ip="${2:-54.232.233.237}"
python3 - "$hostname" "$legacy_ip" <<'PY'
import ipaddress, re, sys
host, ip = sys.argv[1:]
if len(host) > 253 or '.' not in host or not all(
    re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', x)
    for x in host.split('.')
):
    raise SystemExit('HTTPS_HOSTNAME must be a DNS hostname without scheme/path/port')
try:
    ipaddress.ip_address(host)
except ValueError:
    pass
else:
    raise SystemExit('HTTPS_HOSTNAME must not be an IP address')
if ipaddress.ip_address(ip).version != 4:
    raise SystemExit('LEGACY_HTTP_IP must be IPv4')
PY

temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "$temporary_dir"' EXIT

request() {
  local expected="$1" url="$2" kind="$3" status
  shift 3
  # No -L: redirects must never masquerade as a successful API/frontend check.
  # No -k: curl checks the public chain, validity and hostname using host CA roots.
  status="$(curl --noproxy '*' --silent --show-error --fail \
    --connect-timeout 3 --max-time 10 \
    --resolve "$hostname:443:127.0.0.1" \
    --resolve "$hostname:80:127.0.0.1" \
    --resolve "$legacy_ip:80:127.0.0.1" \
    --dump-header "$temporary_dir/headers" \
    --output "$temporary_dir/body" --write-out '%{http_code}' \
    "$@" "$url")" || return 1
  if [[ "$status" != "$expected" ]]; then
    echo "Unexpected HTTP $status (expected $expected): $url" >&2
    return 1
  fi
  python3 - "$temporary_dir" "$kind" "$hostname" <<'PY'
import json, pathlib, re, sys
root, kind, host = sys.argv[1:]
headers = pathlib.Path(root, 'headers').read_text().lower()
body = pathlib.Path(root, 'body').read_text()
if kind == 'frontend':
    assert 'content-type: text/html' in headers and '<html' in body.lower(), 'Expected React HTML'
elif kind == 'health':
    assert 'application/json' in headers and json.loads(body).get('status') == 'UP', 'Backend is not UP'
elif kind == 'catalog':
    assert 'application/json' in headers, 'Expected JSON, not SPA fallback'
    data = json.loads(body)
    assert isinstance(data, list), 'Expected category array'
elif kind.startswith('redirect:'):
    location = re.findall(r'^location:\s*(.*?)\r?$', headers, re.M)
    assert location == [kind.removeprefix('redirect:').lower()], f'Unexpected Location: {location}'
assert not re.search(r'^location:\s*http://', headers, re.M), 'HTTPS downgraded to HTTP'
PY
}

request 200 'http://127.0.0.1:8080/actuator/health' health
request 200 "https://$hostname/" frontend
request 200 "https://$hostname/api/catalog/categories" catalog
# Forged proxy headers must not change the origin or redirect scheme.
request 308 "https://$hostname/api?probe=https-smoke&n=1" 'redirect:/api/?probe=https-smoke&n=1' \
  -H 'X-Forwarded-Proto: http' -H 'X-Forwarded-Host: attacker.invalid' \
  -H 'Forwarded: proto=http;host=attacker.invalid'
request 308 "http://$hostname/api/catalog/categories?probe=https-smoke&n=1" \
  "redirect:https://$hostname/api/catalog/categories?probe=https-smoke&n=1"
request 308 "http://$legacy_ip/api/catalog/categories?probe=https-smoke&n=1" \
  "redirect:https://$hostname/api/catalog/categories?probe=https-smoke&n=1"
echo "HTTPS smoke passed: backend UP, React, JSON API, trusted TLS and redirects."
