#!/usr/bin/env python3
"""Offline validation of Compose JSON on stdin. Never print configuration or secrets."""
import argparse
import base64
import ipaddress
import json
import re
import sys
from urllib.parse import urlsplit
from uuid import UUID


def require(condition, code):
    if not condition:
        raise ValueError(code)


def https_origin(value):
    url = urlsplit(value)
    require(url.scheme == 'https' and url.hostname and url.path in ('', '/')
            and not url.query and not url.fragment and not url.username and not url.password
            and (url.port is None or 1 <= url.port <= 65535), 'INVALID_HTTPS_ORIGIN')
    return (url.hostname.lower(), url.port or 443)


def flag(env, key):
    value = str(env.get(key, 'false')).lower()
    require(value in ('true', 'false'), 'INVALID_INTEGRATION_FLAG')
    return value == 'true'


def validate(config):
    services = config['services']
    env = services['backend']['environment']
    provider = env.get('IDENTITY_PROVIDER', 'MOCK')
    require(provider in ('MOCK', 'M1'), 'INVALID_IDENTITY_PROVIDER')
    outbox = flag(env, 'CORE_OUTBOX_ENABLED')
    inbox = flag(env, 'CORE_INBOX_ENABLED')
    automatic = flag(env, 'CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS')
    simulator = flag(env, 'SIMULATOR_ENABLED')
    approved = env.get('CORE_OUTBOX_APPROVED_EVENT_IDS', '') or ''
    require(not automatic or (outbox and provider == 'M1' and not simulator and not approved.strip()),
            'INVALID_AUTOMATIC_PUBLICATION_MODE')
    if outbox or inbox:
        require(provider == 'M1' and not simulator, 'CORE_REQUIRES_M1_WITHOUT_SIMULATOR')
        require(env.get('CORE_RABBIT_USERNAME', '').strip() and env.get('CORE_RABBIT_PASSWORD', '').strip(),
                'MISSING_CORE_CREDENTIALS')
        require(re.fullmatch(r'[A-Za-z0-9.-]+', str(env.get('CORE_RABBIT_HOST', '')))
                and str(env.get('CORE_RABBIT_PORT')) == '5671' and env.get('CORE_RABBIT_VHOST') == 'core',
                'INVALID_CORE_AMQPS_TARGET')
    if (outbox or inbox) and approved.strip():
        ids = [value.strip() for value in approved.split(',')]
        require(len(ids) <= 1000 and all(str(UUID(value)).lower() == value.lower() and UUID(value).version == 4
                                       for value in ids), 'INVALID_MANUAL_EVENT_IDS')
    if provider == 'M1':
        require(len(base64.b64decode(env.get('APP_JWT_SECRET', ''), validate=True)) >= 32, 'INVALID_M1_JWT_KEY')
        https_origin(env['M1_BASE_URL'])
        origin = https_origin(env['M2_AUTH_BROWSER_ORIGIN'])
        if 'M2_PUBLIC_BASE_URL' in env:
            require(https_origin(env['M2_PUBLIC_BASE_URL']) == origin, 'M2_ORIGIN_MISMATCH')
    private = {'APP_JWT_SECRET', 'CORE_RABBIT_USERNAME', 'CORE_RABBIT_PASSWORD', 'TRACKING_CODE_ENCRYPTION_KEY_BASE64'}
    for name, service in services.items():
        if name != 'backend':
            require(not private.intersection(service.get('environment', {})), 'PRIVATE_VARIABLE_OUTSIDE_BACKEND')
        require(not private.intersection(service.get('build', {}).get('args', {})), 'PRIVATE_BUILD_ARGUMENT')
    build = services.get('frontend', {}).get('build', {})
    if build:
        args = build.get('args', {})
        require(args.get('VITE_API_BASE_URL') == '/api' and args.get('VITE_IDENTITY_PROVIDER') == provider,
                'FRONTEND_BUILD_MODE_MISMATCH')
    if 'caddy' not in services:
        return None
    proxy = services['caddy']['environment']
    host, ip = proxy['HTTPS_HOSTNAME'], proxy['LEGACY_HTTP_IP']
    require(len(host) <= 253 and '.' in host and all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', part)
            for part in host.split('.')) and not re.fullmatch(r'[0-9.]+', host), 'INVALID_HTTPS_HOSTNAME')
    require(ipaddress.ip_address(ip).version == 4, 'INVALID_LEGACY_IPV4')
    if provider == 'M1':
        require(https_origin('https://' + host) == origin, 'M2_PROXY_ORIGIN_MISMATCH')
    return host, ip


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--proxy-settings', action='store_true', help='Print only validated public hostname and legacy IPv4')
    output.add_argument('--deployment-settings', action='store_true', help='Print public proxy settings and Backend provider')
    args = parser.parse_args()
    try:
        config = json.load(sys.stdin)
        proxy = validate(config)
        if args.proxy_settings or args.deployment_settings:
            require(proxy is not None, 'MISSING_PROXY_SETTINGS')
            if args.deployment_settings:
                print(*proxy, config['services']['backend']['environment'].get('IDENTITY_PROVIDER', 'MOCK'))
            else:
                print(*proxy)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        # Parser exceptions can contain rejected secret values; never interpolate them.
        print('Integration configuration rejected; check M1/Core modes, private credentials and HTTPS origins.', file=sys.stderr)
        return 78
    return 0


if __name__ == '__main__':
    sys.exit(main())
