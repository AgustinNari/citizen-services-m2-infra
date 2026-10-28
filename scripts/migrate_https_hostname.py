"""Operator-only, Linux hostname cutover. Invoke through migrate-https-hostname.sh.

No GitHub writes, DNS updates, certificate deletion, image pulls or database writes.
Private backups intentionally include runtime secrets and must never be published.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

INPUTS = ('compose/.env', 'compose/images.env', 'compose/compose.deploy.yml',
          'caddy/Caddyfile', 'scripts/deploy-service.sh', 'scripts/smoke-https.sh')
SERVICES = ('postgres', 'frontend', 'backend', 'caddy')
RESOLVERS = ('https://dns.google/resolve', 'https://cloudflare-dns.com/dns-query')


class MigrationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise MigrationError(message)


def hostname(value):
    value = value.lower()
    require(len(value) <= 253 and '.' in value and all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
        for part in value.split('.')), 'Use a DNS hostname without scheme/path/port')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise MigrationError('A DNS hostname is required, not an IP address')


def edit_hostname(data, new_host):
    matches = list(re.finditer(
        rb'(?m)^([ \t]*(?:export[ \t]+)?HTTPS_HOSTNAME[ \t]*=[ \t]*)[^\r\n]*', data))
    require(len(matches) == 1, 'Expected exactly one HTTPS_HOSTNAME assignment in .env')
    match = matches[0]
    return data[:match.start()] + match[1] + hostname(new_host).encode('ascii') + data[match.end():]


def atomic_write(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_dns(resolver, name, kind):
    url = resolver + '?' + urllib.parse.urlencode({'name': name, 'type': kind})
    request = urllib.request.Request(url, headers={'Accept': 'application/dns-json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def check_dns(new_host, expected_ip, fetch=fetch_dns):
    hostname(new_host)
    require(ipaddress.ip_address(expected_ip).version == 4, 'Expected a public IPv4')
    require(ipaddress.ip_address(expected_ip).is_global, 'Expected a public IPv4')
    ancestors = ['.'.join(new_host.split('.')[i:]) for i in range(len(new_host.split('.')))]
    evidence = []
    for resolver in RESOLVERS:
        for name, kind in [(new_host, 'A'), (new_host, 'AAAA')] + [(x, 'CAA') for x in ancestors]:
            data = fetch(resolver, name, kind)
            require(data.get('Status') == 0, f'DNS {kind} failed for {name} at {resolver}')
            answers = data.get('Answer', [])
            if kind == 'A':
                require(answers and all(x.get('type') == 1 and x.get('data') == expected_ip
                                        for x in answers), 'A must point only to the existing static IP')
            elif kind == 'AAAA':
                require(not answers, 'Unexpected AAAA/CNAME: review IPv6 before proceeding')
            else:
                # Fail closed rather than silently misinterpreting a CAA policy or wrong RR type.
                require(not answers, f'CAA response at {name} requires manual review; no cutover')
            evidence.append({'resolver': resolver, 'name': name, 'type': kind,
                             'status': data['Status'], 'answers': answers})
    return evidence


def verify_tls(host, minimum_seconds=0):
    context = ssl.create_default_context()
    with socket.create_connection(('127.0.0.1', 443), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=host) as connection:
            certificate = connection.getpeercert()
    remaining = ssl.cert_time_to_seconds(certificate['notAfter']) - time.time()
    require(remaining >= minimum_seconds, 'Certificate expires too soon for a safe rollback window')


class Runner:
    def __call__(self, *args, env=None):
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=90)
        # Compose output can include secrets; do not echo it on failure.
        require(result.returncode == 0, f'{Path(args[0]).name} command failed (exit {result.returncode}); inspect privately')
        return result.stdout


class Migration:
    def __init__(self, root, state_dir, run=None, dns=None, tls=None, sleep=time.sleep):
        self.root = root.resolve()
        self.state = state_dir.resolve()
        self.marker = self.state / 'm2-maintenance'
        self.run = run or Runner()
        self.dns = dns or check_dns
        self.tls = tls or verify_tls
        self.sleep = sleep
        self.project = None
        self.env = os.environ.copy()
        for key in ('COMPOSE_PROJECT_NAME', 'COMPOSE_FILE', 'COMPOSE_PROFILES',
                    'HTTPS_HOSTNAME', 'LEGACY_HTTP_IP'):
            self.env.pop(key, None)

    def dc(self, *args, host=None, default_project=False):
        command = ['docker', 'compose', '--project-directory', str(self.root / 'compose'),
                   '--env-file', str(self.root / 'compose/.env'),
                   '--env-file', str(self.root / 'compose/images.env'),
                   '-f', str(self.root / 'compose/compose.deploy.yml')]
        if not default_project:
            command += ['--project-name', self.project]
        env = self.env.copy()
        if host:
            env['HTTPS_HOSTNAME'] = host
        return self.run(*command, *args, env=env)

    def config(self, **kwargs):
        return json.loads(self.dc('config', '--format', 'json', **kwargs))

    def inspect(self, identifier):
        values = json.loads(self.run('docker', 'inspect', identifier, env=self.env))
        require(len(values) == 1, 'Ambiguous container')
        return values[0]

    def discover(self, selected=None):
        identifiers = [selected] if selected else self.run(
            'docker', 'ps', '--filter', 'label=com.docker.compose.service=backend',
            '--format', '{{.ID}}', env=self.env).split()
        candidates = []
        for identifier in identifiers:
            item = self.inspect(identifier)
            labels = item['Config'].get('Labels', {})
            if (labels.get('com.docker.compose.service') == 'backend' and
                    labels.get('com.docker.compose.project.working_dir') == str(self.root / 'compose')):
                candidates.append(labels['com.docker.compose.project'])
        require(len(candidates) == 1, 'Cannot identify one existing project; use --backend-container after review')
        self.project = candidates[0]
        require(self.config(default_project=True)['name'] == self.project,
                'Normal CD would use another Compose project; resolve separately before cutover')

    def services(self, allow_stopped=False):
        result = {}
        for name in SERVICES:
            identifiers = self.dc('ps', '-aq', name).split()
            require(len(identifiers) == 1, f'Expected exactly one existing {name} container')
            item = self.inspect(identifiers[0])
            require(item['State']['Running'] or (allow_stopped and name in ('backend', 'caddy')),
                    f'{name} is not running')
            require(item['Config']['Labels']['com.docker.compose.project'] == self.project,
                    'Container belongs to another project')
            mounts = [{key: mount.get(key) for key in ('Type', 'Name', 'Source', 'Destination', 'RW')}
                      for mount in item['Mounts']]
            result[name] = {'id': item['Id'], 'image': item['Image'], 'reference': item['Config']['Image'],
                            'mounts': sorted(mounts, key=lambda value: value['Destination']),
                            'env': dict(x.split('=', 1) for x in item['Config']['Env'])}
        return result

    def cached(self, services):
        for name, service in services.items():
            image_id = self.run('docker', 'image', 'inspect', '--format', '{{.Id}}',
                                service['reference'], env=self.env).strip()
            require(image_id == service['image'], f'{name} image missing or cached tag differs; no pull is allowed')

    def smoke(self, host):
        self.run('bash', str(self.root / 'scripts/smoke-https.sh'), host, self.ip, env=self.env)

    def preflight(self, target, selected=None):
        require(not self.marker.exists(), 'Maintenance already active; use check/rollback/finish for its owner')
        for relative in INPUTS:
            require((self.root / relative).is_file() and not (self.root / relative).is_symlink(),
                    f'Missing or symlinked input: {relative}')
        self.discover(selected)
        current = self.config()
        old = hostname(current['services']['caddy']['environment']['HTTPS_HOSTNAME'])
        self.ip = current['services']['caddy']['environment']['LEGACY_HTTP_IP']
        original = (self.root / 'compose/.env').read_bytes()
        edit_hostname(original, target)  # Validate exactly one key before any write.
        future = self.config(host=target)
        expected = json.loads(json.dumps(current))
        expected['services']['caddy']['environment']['HTTPS_HOSTNAME'] = target
        expected['services']['backend']['environment']['M2_PUBLIC_BASE_URL'] = 'https://' + target
        require(future == expected, 'Hostname changes configuration beyond the two intended variables')
        services = self.services()
        for name in SERVICES:
            require(current['services'][name].get('image') == services[name]['reference'],
                    f'{name} Compose image differs from the running reference; reconcile before cutover')
        for name in ('backend', 'caddy'):
            require(all(services[name]['env'].get(key) == str(value)
                        for key, value in current['services'][name]['environment'].items()),
                    f'{name} runtime differs from Compose; reconcile before cutover')
        self.cached(services)
        dns = self.dns(target, self.ip)
        self.tls(old, 86400)
        self.smoke(old)
        self.run('docker', 'run', '--rm', '--network', 'none', '--pull', 'never',
                 '-e', 'HTTPS_HOSTNAME=' + target, '-e', 'LEGACY_HTTP_IP=' + self.ip,
                 '--mount', f'type=bind,source={self.root / "caddy/Caddyfile"},target=/etc/caddy/Caddyfile,readonly',
                 services['caddy']['image'], 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile',
                 '--adapter', 'caddyfile', env=self.env)
        return {'root': str(self.root), 'project': self.project, 'old': old, 'new': target,
                'ip': self.ip, 'config': current, 'services': services, 'dns': dns,
                'hashes': {relative: digest(self.root / relative) for relative in INPUTS},
                'attempted': [], 'phase': 'prepared'}

    def save(self, backup, record):
        atomic_write(backup / 'state.private.json', json.dumps(record, indent=2).encode())

    def owner(self, backup):
        require(self.marker.is_file() and not self.marker.is_symlink() and
                self.marker.read_text().strip() == str(backup),
                'Maintenance marker is absent or owned by another operation')

    def load(self, backup):
        backup = backup.resolve()
        require(backup.parent == self.state and backup.name.startswith('hostname-') and
                stat.S_IMODE(backup.stat().st_mode) & 0o077 == 0,
                'Use a private hostname backup inside the shared deployment state directory')
        record = json.loads((backup / 'state.private.json').read_text())
        require(record['root'] == str(self.root), 'Backup belongs to another repository')
        require(set(record['hashes']) == set(INPUTS), 'Unexpected backup file list')
        for relative, value in record['hashes'].items():
            require(digest(backup / relative) == value, 'Backup content changed; do not overwrite runtime')
        self.project, self.ip = record['project'], record['ip']
        return backup, record

    def check_files(self, backup, record, host):
        for relative in INPUTS:
            original = (backup / relative).read_bytes()
            expected = edit_hostname(original, host) if relative == 'compose/.env' and host != record['old'] else original
            require((self.root / relative).read_bytes() == expected,
                    f'{relative} changed beyond this migration; inspect privately')

    def check_services(self, record, host):
        current = self.services()
        for name, before in record['services'].items():
            expected = json.loads(json.dumps(before))
            if name == 'backend':
                expected['env']['M2_PUBLIC_BASE_URL'] = 'https://' + host
            elif name == 'caddy':
                expected['env']['HTTPS_HOSTNAME'] = host
            if name in ('backend', 'caddy'):
                expected['id'] = current[name]['id']
            require(current[name] == expected, f'{name} image/runtime/mounts or untouched container identity changed')

    def wait(self, action, description):
        for attempt in range(12):
            try:
                action()
                return
            except (MigrationError, OSError, ssl.SSLError, subprocess.TimeoutExpired):
                if attempt == 11:
                    raise MigrationError(description + ' failed; keep maintenance and use explicit rollback')
                self.sleep(3)

    def check(self, backup, record, host):
        self.check_files(backup, record, host)
        expected = json.loads(json.dumps(record['config']))
        expected['services']['caddy']['environment']['HTTPS_HOSTNAME'] = host
        expected['services']['backend']['environment']['M2_PUBLIC_BASE_URL'] = 'https://' + host
        require(self.config() == expected, 'Rendered Compose differs from the preserved configuration')
        self.check_services(record, host)
        self.tls(host)
        self.smoke(host)

    def apply(self, target, selected=None):
        record = self.preflight(target, selected)
        if record['old'] == target:
            print('Already using the target hostname; preflight passed, no changes made.')
            return
        backup = self.state / ('hostname-' + time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
        backup.mkdir(mode=0o700)
        for relative in INPUTS:
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(self.root / relative, destination)
            os.chmod(destination, 0o600)
            require(digest(destination) == record['hashes'][relative], 'Input changed during backup; rerun preflight')
        self.save(backup, record)
        fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(str(backup) + '\n')
        atomic_write(self.state / 'hostname-last-backup', (str(backup) + '\n').encode())
        print('Private backup:', backup, flush=True)
        try:
            self.check_files(backup, record, record['old'])
            path = self.root / 'compose/.env'
            atomic_write(path, edit_hostname(path.read_bytes(), target), stat.S_IMODE(path.stat().st_mode))
            self.check_files(backup, record, target)
            self.dc('config', '--quiet')
            for service in ('caddy', 'backend'):
                record['attempted'].append(service)
                self.save(backup, record)  # Persist before up, including partially failed recreation.
                self.dc('up', '-d', '--no-deps', '--pull', 'never', service)
                if service == 'caddy':
                    self.wait(lambda: self.tls(target), 'New public TLS')
                else:
                    self.wait(lambda: self.smoke(target), 'New HTTPS smoke')
            self.check(backup, record, target)
            record['phase'] = 'applied'
            self.save(backup, record)
            print('Cutover checks passed. Maintenance retained until public/browser acceptance and finish.')
        except BaseException:
            record['phase'] = 'failed'
            self.save(backup, record)
            print('Stopped. Maintenance retained. Run explicit rollback with --backup', backup, flush=True)
            raise

    def rollback(self, backup, record):
        if record['phase'] == 'finished' and not self.marker.exists():
            fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output:
                output.write(str(backup) + '\n')
        self.owner(backup)
        if record['phase'] == 'rolled-back' or record.get('accepted_host') == record['old']:
            self.check(backup, record, record['old'])
            print('Already rolled back; no services recreated.')
            return
        # Refuse to overwrite unrelated concurrent edits; stored configs remain available privately.
        for relative in INPUTS:
            if relative != 'compose/.env':
                require(digest(self.root / relative) == record['hashes'][relative],
                        'Configuration changed outside migration; inspect backup before manual recovery')
        original = (backup / 'compose/.env').read_bytes()
        require((self.root / 'compose/.env').read_bytes() in (original, edit_hostname(original, record['new'])),
                'Runtime file has unrelated edits; refusing to overwrite them')
        self.cached(record['services'])
        current = self.services(allow_stopped=True)
        for name, before in record['services'].items():
            expected = json.loads(json.dumps(before))
            if name in ('backend', 'caddy'):
                key = 'M2_PUBLIC_BASE_URL' if name == 'backend' else 'HTTPS_HOSTNAME'
                allowed = [('https://' + host) if name == 'backend' else host
                           for host in (record['old'], record['new'])]
                require(current[name]['env'].get(key) in allowed, 'Runtime hostname changed outside migration')
                expected['env'][key] = current[name]['env'][key]
                expected['id'] = current[name]['id']
            require(current[name] == expected, 'Images, mounts or runtime changed outside migration; inspect before rollback')
        record.pop('accepted_host', None)
        record['phase'] = 'rolling-back'
        self.save(backup, record)
        atomic_write(self.root / 'compose/.env', original, stat.S_IMODE((self.root / 'compose/.env').stat().st_mode))
        self.dc('config', '--quiet')
        for service in record['attempted']:
            self.dc('up', '-d', '--no-deps', '--pull', 'never', service)
        self.wait(lambda: self.tls(record['old']), 'Restored TLS')
        self.wait(lambda: self.smoke(record['old']), 'Restored HTTPS smoke')
        self.check(backup, record, record['old'])
        record['phase'] = 'rolled-back'
        self.save(backup, record)
        print('Rollback verified; maintenance retained until operator acceptance and finish.')

    def finish(self, backup, record):
        require(record['phase'] in ('applied', 'rolled-back', 'finished'), 'Use check/rollback before accepting a failed cutover')
        host = record.get('accepted_host') or (record['old'] if record['phase'] == 'rolled-back' else record['new'])
        self.check(backup, record, host)
        if record['phase'] != 'finished':
            self.owner(backup)
            self.marker.unlink()
            record.update(phase='finished', accepted_host=host)
            self.save(backup, record)
        else:
            require(not self.marker.exists(), 'Another maintenance operation is active')
        print('Accepted; own maintenance marker removed. GitHub CD variables were not changed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True, help=argparse.SUPPRESS)
    parser.add_argument('mode', choices=('preflight', 'apply', 'check', 'rollback', 'finish'))
    parser.add_argument('--hostname', type=hostname)
    parser.add_argument('--backend-container')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--confirm-hostname')
    parser.add_argument('--cd-disabled-and-drained', action='store_true',
                        help='Operator attests both GitHub CD variables are false and all runs have drained; not verified by this script')
    parser.add_argument('--confirm-rollback', action='store_true')
    parser.add_argument('--accept', action='store_true', help='Operator accepted external TLS, login and attachments')
    args = parser.parse_args()
    require(os.environ.get('M2_HOSTNAME_LOCK_HELD') == '1', 'Invoke through migrate-https-hostname.sh to acquire flock')
    require(args.state_dir.resolve() == (Path.home() / 'DA2/.deploy').resolve(), 'Unexpected shared deployment state directory')
    if args.mode in ('preflight', 'apply'):
        require(args.hostname, '--hostname is required')
    if args.mode == 'apply':
        require(args.confirm_hostname == args.hostname and args.cd_disabled_and_drained,
                'Apply requires --confirm-hostname matching the target and --cd-disabled-and-drained')
    if args.mode == 'rollback':
        require(args.confirm_rollback and args.cd_disabled_and_drained,
                'Rollback requires --confirm-rollback and --cd-disabled-and-drained')
    if args.mode == 'finish':
        require(args.accept and args.cd_disabled_and_drained,
                'Finish requires --accept and --cd-disabled-and-drained')
    migration = Migration(Path(__file__).resolve().parents[1], args.state_dir)
    if args.mode == 'preflight':
        record = migration.preflight(args.hostname, args.backend_container)
        print('Preflight passed:', record['project'], record['old'], '->', record['new'])
        print('No services changed. CD state and public ingress still require external operator verification.')
    elif args.mode == 'apply':
        migration.apply(args.hostname, args.backend_container)
    else:
        require(args.backup, '--backup is required; use the printed private backup path')
        backup, record = migration.load(args.backup)
        if args.mode == 'check':
            host = record.get('accepted_host') or (record['old'] if record['phase'] == 'rolled-back' else record['new'])
            migration.check(backup, record, host)
            print('HTTPS and preserved state verified for', host)
        elif args.mode == 'rollback':
            migration.rollback(backup, record)
        else:
            migration.finish(backup, record)


if __name__ == '__main__':
    try:
        main()
    except (MigrationError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print('STOP:', str(error) if isinstance(error, MigrationError) else type(error).__name__, flush=True)
        raise SystemExit(1)
