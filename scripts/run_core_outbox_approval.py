#!/usr/bin/env python3
"""GitHub runner client. User input is data on SSH stdin, never remote shell code."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from core_outbox_approval import CODES, MAX_INPUT, STATUSES, Rejected, normalize_ids, require


def validate_report(raw, ids):
    require(len(raw.encode('utf-8')) <= MAX_INPUT)
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == {
        'version', 'code', 'env_written', 'recreated', 'health', 'events'})
    require(type(value['version']) is int and value['version'] == 1 and value['code'] in CODES)
    require(type(value['env_written']) is bool and type(value['recreated']) is bool)
    require(value['health'] in {'UP', 'UNVERIFIED'} and isinstance(value['events'], list))
    events = value['events']
    require(len(events) == len(ids))
    for event, event_id in zip(events, ids):
        require(isinstance(event, dict) and set(event) == {'id', 'status'}
                and event['id'] == event_id and event['status'] in STATUSES)
    require(value['code'] != 'PUBLISHED' or (value['health'] == 'UP'
            and all(event['status'] == 'PUBLISHED' for event in events)))
    return value


def ssh_request(payload, settings, *, command='approve-core-outbox-v1', timeout=1550):
    require(command in {'approve-core-outbox-v1', 'query-core-outbox-v1'})
    host, user = settings.get('LIGHTSAIL_HOST', ''), settings.get('LIGHTSAIL_USER', '')
    require(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host))
    require(re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user))
    require(settings.get('OUTBOX_APPROVALS_ENABLED') == 'true')
    key, known_hosts = settings.get('OUTBOX_SSH_PRIVATE_KEY', ''), settings.get('LIGHTSAIL_KNOWN_HOSTS', '')
    require(key.strip() and known_hosts.strip())
    with tempfile.TemporaryDirectory(prefix='m2-outbox-ssh-') as directory:
        root = Path(directory)
        root.chmod(0o700)
        private_key, pinned_hosts = root / 'key', root / 'known_hosts'
        private_key.write_text(key.rstrip() + '\n', encoding='utf-8')
        pinned_hosts.write_text(known_hosts.rstrip() + '\n', encoding='utf-8')
        private_key.chmod(0o600)
        pinned_hosts.chmod(0o600)
        # Do not send the key in the SSH child's environment.
        child_env = {key: settings[key] for key in ('PATH', 'HOME', 'LANG') if key in settings}
        return subprocess.run([
            'ssh', '-F', '/dev/null', '-T', '-i', str(private_key),
            '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(pinned_hosts),
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'LogLevel=ERROR', '-l', user, host, command,
        ], input=json.dumps(payload), text=True, capture_output=True,
            timeout=timeout, check=False, env=child_env)


def connect(ids, settings):
    result = ssh_request({'event_ids': ids}, settings)
    # Never print raw SSH stdout/stderr (including banners or remote diagnostics).
    report = validate_report(result.stdout, ids)
    require(result.returncode == 0 if report['code'] == 'PUBLISHED' else result.returncode != 0)
    return report


def summary(report):
    lines = [f"Resultado: **{report['code']}**", '',
             f"Disponibilidad Backend/HTTPS: **{report['health']}**. "
             f"`.env` actualizado: **{str(report['env_written']).lower()}**. "
             f"Recreación de Backend confirmada: **{str(report['recreated']).lower()}**.", '',
             '| Evento | Estado observado |', '| --- | --- |']
    lines.extend(f"| `{event['id']}` | {event['status']} |" for event in report['events'])
    lines += ['', '`PUBLISHED` proviene del estado persistido por Backend después del confirm de RabbitMQ; '
              'no acredita procesamiento por consumidores. `PENDING` no es publicación confirmada.', '',
              'Ante error o desconexión, puede haber publicaciones parciales. Consultar/repetir los mismos UUID; '
              'no restablecer estados SQL ni asumir que cancelar Actions revoca una aprobación.', '',
              'Operación y recuperación: `docs/core-outbox-approval.md`.']
    return '\n'.join(lines) + '\n'


def main():
    try:
        ids = normalize_ids(os.environ.get('OUTBOX_EVENT_IDS', ''))
    except (Rejected, ValueError):
        print('::error::UUID input rejected: use 1–50 distinct RFC UUID v4 values separated by commas or newlines.')
        return 1
    try:
        report = connect(ids, os.environ)
        rendered = summary(report)
        print(rendered)
        exit_code = 0 if report['code'] == 'PUBLISHED' else 1
        if exit_code:
            print('::error::Approval did not confirm publication of every requested event. See the summary.')
    except Exception:
        # Exceptions may embed keys, hostnames, SQL output or transport banners.
        rendered = ('Resultado: **UNVERIFIED**. No se pudo verificar una respuesta segura del servidor.\n\n'
                    'Comprobar habilitación, configuración SSH y scripts instalados. '
                    'La operación puede haber quedado parcialmente aplicada; repetir los mismos UUID para consultar/reconciliar.\n\n'
                    '| Evento | Estado observado |\n| --- | --- |\n' +
                    ''.join(f'| `{event_id}` | UNKNOWN |\n' for event_id in ids))
        print('::error::Outbox result unverified; check setup or retry the same IDs. Remote details were suppressed.')
        exit_code = 1
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(rendered)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
