#!/usr/bin/env python3
"""Server-side, allowlist-only Outbox approval. Invoke through approve-core-outbox.sh.

No dotenv sourcing, SQL writes, payload selection, image pulls or service logs.
All command output stays private; stdout is a small, bounded JSON protocol.
"""
import copy
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from uuid import UUID

APPROVED = 'CORE_OUTBOX_APPROVED_EVENT_IDS'
ENABLED = 'CORE_OUTBOX_ENABLED'
AUTOMATIC = 'CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS'
MAX_BATCH = 50
MAX_APPROVED = 1000
MAX_INPUT = 65536
POLL_SECONDS = 180
HEALTH_SECONDS = 180
CODES = frozenset({
    'PUBLISHED', 'PENDING', 'FAILED', 'INVALID_INPUT', 'BATCH_REJECTED',
    'CONFIGURATION_REJECTED', 'BACKEND_DRIFT', 'HEALTH_PREFLIGHT_FAILED',
    'HEALTH_FAILED', 'APPLY_FAILED', 'DATABASE_UNAVAILABLE', 'INTERNAL_ERROR',
})
STATUSES = frozenset({'PENDING', 'PUBLISHED', 'FAILED', 'NOT_FOUND', 'UNKNOWN'})


class Rejected(Exception):
    pass


def require(condition, code='CONFIGURATION_REJECTED'):
    if not condition:
        raise Rejected(code)


def normalize_ids(value, limit=MAX_BATCH, allow_empty=False):
    """Canonical RFC UUID v4 only, matching Backend; reject empty comma tokens."""
    require(isinstance(value, str) and len(value.encode('utf-8')) <= MAX_INPUT, 'INVALID_INPUT')
    if allow_empty and not value.strip():
        return []
    ids = []
    # A newline is an alternative separator. Blank lines are harmless; empty comma fields aren't.
    for line in value.replace('\r\n', '\n').split('\n'):
        if not line.strip():
            continue
        for token in line.split(','):
            token = token.strip()
            require(re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}', token), 'INVALID_INPUT')
            canonical = str(UUID(token))
            if canonical not in ids:
                ids.append(canonical)
    require((ids or allow_empty) and len(ids) <= limit, 'INVALID_INPUT')
    return ids


def request_ids(raw):
    try:
        value = json.loads(raw)
        require(isinstance(value, dict) and set(value) == {'event_ids'}, 'INVALID_INPUT')
        ids = value['event_ids']
        require(isinstance(ids, list) and 1 <= len(ids) <= MAX_BATCH and all(isinstance(x, str) for x in ids), 'INVALID_INPUT')
        # Lists cannot smuggle a separator within a UUID.
        require(all(not re.search(r'[,\s]', x) for x in ids), 'INVALID_INPUT')
        return normalize_ids(','.join(ids))
    except (ValueError, TypeError):
        raise Rejected('INVALID_INPUT') from None


def run_private(args, *, data=None, timeout=45, env=None):
    """Never let subprocess errors carry credentials/config/payloads into Actions."""
    try:
        result = subprocess.run(args, input=data, text=True, capture_output=True,
                                timeout=timeout, env=env, check=False)
        require(result.returncode == 0)
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        raise Rejected('CONFIGURATION_REJECTED') from None


def env_candidate(original, ids):
    """Replace one simple assignment; every other byte is retained, including CRLF."""
    require(b'\x00' not in original)
    lines = original.splitlines(keepends=True)
    key = APPROVED.encode('ascii')
    matches = [i for i, line in enumerate(lines)
               if re.match(rb'^\s*(?:export\s+)?' + key + rb'\s*=', line)]
    require(len(matches) <= 1)
    if matches:
        index = matches[0]
        # Multiline/reference values cannot be edited safely by a line replacement.
        old = lines[index].split(b'=', 1)[1].strip()
        require(re.fullmatch(rb'''(?:[0-9a-fA-F, \t-]*|'[0-9a-fA-F, \t-]*'|"[0-9a-fA-F, \t-]*")(?:\s+#.*)?''', old))
        ending = b'\r\n' if lines[index].endswith(b'\r\n') else b'\n'
        lines[index] = key + b'=' + ','.join(ids).encode('ascii') + ending
        return b''.join(lines)
    ending = b'\r\n' if b'\r\n' in original else b'\n'
    return original + (ending if original and not original.endswith(b'\n') else b'') + key + b'=' + ','.join(ids).encode('ascii') + ending


def private_file(path):
    require(path.is_file() and not path.is_symlink())
    info = path.stat()
    # A private .env must not become more accessible as a side effect of approval.
    require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.geteuid())
    return info


def atomic_write(path, content):
    """Same directory and owner, mode 0600, fsync before atomic rename."""
    descriptor, temporary = tempfile.mkstemp(prefix='.env.outbox.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Host:
    def __init__(self, root):
        self.root = Path(root)
        self.compose_dir = self.root / 'compose'
        self.runtime = self.compose_dir / '.env'
        self.images = self.compose_dir / 'images.env'
        self.child_env = os.environ.copy()
        # Shell overrides cannot silently change Compose's effective runtime/project.
        # Only process essentials survive; Compose loads the two explicit files.
        self.child_env = {key: self.child_env[key] for key in ('PATH', 'HOME', 'LANG') if key in self.child_env}

    def compose(self, *args, runtime=None, data=None, timeout=45, overrides=None):
        return run_private([
            'docker', 'compose', '--project-directory', str(self.compose_dir),
            '--env-file', str(runtime or self.runtime), '--env-file', str(self.images),
            '-f', str(self.compose_dir / 'compose.deploy.yml'), *args,
        ], data=data, timeout=timeout, env=self.child_env | (overrides or {}))

    def config(self, runtime=None):
        text = self.compose('config', '--format', 'json', runtime=runtime)
        run_private(['python3', str(self.root / 'scripts/validate-integration-config.py')],
                    data=text, env=self.child_env)
        config = json.loads(text)
        env = config['services']['backend']['environment']
        require(env.get(ENABLED) == 'true' and env.get(AUTOMATIC) == 'false')
        require(re.fullmatch(r'(?:[1-9]|1[0-9]|20)', str(env.get('CORE_OUTBOX_MAX_ATTEMPTS', ''))))
        require(config.get('name') and 'caddy' in config['services'])
        return config

    def running(self, config):
        container_id = self.compose('ps', '-q', 'backend').strip()
        require(re.fullmatch(r'[0-9a-f]{12,64}', container_id), 'BACKEND_DRIFT')
        info = json.loads(run_private(['docker', 'inspect', container_id], env=self.child_env))[0]
        expected = config['services']['backend']
        require(info['State']['Running'] is True and info['Config']['Image'] == expected['image'], 'BACKEND_DRIFT')
        labels = info['Config']['Labels']
        require(labels['com.docker.compose.project'] == config['name']
                and labels['com.docker.compose.service'] == 'backend', 'BACKEND_DRIFT')
        actual = dict(item.split('=', 1) for item in info['Config']['Env'])
        # Prevent approval from applying unrelated, unapplied .env changes.
        require(all(actual.get(key) == str(value) for key, value in expected['environment'].items()
                    if key != APPROVED), 'BACKEND_DRIFT')
        running_ids = normalize_ids(actual.get(APPROVED, ''), MAX_APPROVED, allow_empty=True)
        # Check the ENTIRE Backend service (mounts, ports, networks, etc.), substituting
        # only its running allowlist when hashing. This prevents accidental config deploys.
        baseline_hash = self.compose('config', '--hash', 'backend',
                                     overrides={APPROVED: actual.get(APPROVED, '')}).split()
        service_hash = labels.get('com.docker.compose.config-hash', '')
        require(re.fullmatch(r'[0-9a-f]{64}', service_hash)
                and baseline_hash == ['backend', service_hash], 'BACKEND_DRIFT')
        # --pull never must also use exactly the cached image already running.
        cached_id = run_private(['docker', 'image', 'inspect', '--format', '{{.Id}}', expected['image']],
                                env=self.child_env).strip()
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', cached_id) and cached_id == info['Image'], 'BACKEND_DRIFT')
        return running_ids

    def rows(self, ids):
        if not ids:
            return {}
        # Only canonical validated UUID literals enter this fixed, read-only query.
        require(len(ids) <= 2 * MAX_APPROVED + MAX_BATCH)
        # Disk + running allowlists can exceed the public request's byte limit while
        # reconciling a partial attempt. Bound the count and validate each literal.
        require(all(normalize_ids(value) == [value] for value in ids), 'INVALID_INPUT')
        literals = ','.join("'" + value + "'::uuid" for value in ids)
        sql = ("BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; "
               "SELECT event_id::text, status, retry_count, (published_at IS NOT NULL), event_type "
               "FROM public.outbox_events WHERE event_id IN (" + literals + "); COMMIT;\n")
        try:
            raw = self.compose('exec', '-T', 'postgres', 'sh', '-c',
                               'exec psql -X -q -A -t -F "|" -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                               data=sql)
            rows = {}
            for line in raw.splitlines():
                event_id, status, attempts, published, event_type = line.split('|')
                require(event_id in ids and event_id not in rows and status in {'PENDING', 'PUBLISHED', 'FAILED'})
                require(attempts.isdecimal() and int(attempts) <= 2147483647 and published in {'t', 'f'})
                require(status != 'PUBLISHED' or published == 't')
                rows[event_id] = {'status': status, 'attempts': int(attempts), 'type': event_type}
            return rows
        except (Rejected, ValueError):
            raise Rejected('DATABASE_UNAVAILABLE') from None

    def healthy(self, config):
        try:
            body = run_private(['curl', '--noproxy', '*', '--silent', '--fail', '--max-time', '10',
                                'http://127.0.0.1:8080/actuator/health'], env=self.child_env)
            health = json.loads(body)
            require(isinstance(health, dict) and health.get('status') == 'UP')
            proxy = config['services']['caddy']['environment']
            run_private(['bash', str(self.root / 'scripts/smoke-https.sh'),
                         proxy['HTTPS_HOSTNAME'], proxy['LEGACY_HTTP_IP']], timeout=80, env=self.child_env)
            return True
        except (Rejected, ValueError, KeyError):
            return False

    def apply(self):
        self.compose('up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'backend', timeout=120)


def approve(host, ids, *, sleep=time.sleep, monotonic=time.monotonic):
    report = {'version': 1, 'code': 'INTERNAL_ERROR', 'env_written': False,
              'recreated': False, 'health': 'UNVERIFIED',
              'events': [{'id': value, 'status': 'UNKNOWN'} for value in ids]}

    def record(rows):
        report['events'] = [{'id': value, 'status': rows.get(value, {}).get('status', 'NOT_FOUND')} for value in ids]

    changed = False
    try:
        private_file(host.runtime)
        original = host.runtime.read_bytes()
        images = host.images.read_bytes()
        config = host.config()
        env = config['services']['backend']['environment']
        disk_ids = normalize_ids(env.get(APPROVED, '') or '', MAX_APPROVED, allow_empty=True)
        running_ids = host.running(config)
        all_ids = sorted(set(ids + disk_ids + running_ids))
        rows = host.rows(all_ids)
        record(rows)
        maximum = int(env['CORE_OUTBOX_MAX_ATTEMPTS'])
        require(all(value in rows and (rows[value]['status'] == 'PUBLISHED'
                    or (rows[value]['status'] == 'PENDING' and rows[value]['attempts'] < maximum
                        and rows[value]['type'] in {'ticketCreated', 'ticketUpdated'}))
                    for value in ids), 'BATCH_REJECTED')
        # Preserve pending approvals from BOTH disk and running container after partial attempts.
        target = sorted(value for value in all_ids if value in rows and rows[value]['status'] == 'PENDING')
        require(len(target) <= MAX_APPROVED)
        require(host.healthy(config), 'HEALTH_PREFLIGHT_FAILED')
        report['health'] = 'UP'
        candidate = env_candidate(original, target)
        descriptor, temporary = tempfile.mkstemp(prefix='.env.outbox.', dir=host.compose_dir)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(candidate)
            candidate_config = host.config(Path(temporary))
            expected = copy.deepcopy(config)
            expected['services']['backend']['environment'][APPROVED] = ','.join(target)
            require(candidate_config == expected)
        finally:
            os.unlink(temporary)
        # Also detect manual edits that did not respect the deployment lock.
        require(host.runtime.read_bytes() == original and host.images.read_bytes() == images)
        if candidate != original:
            atomic_write(host.runtime, candidate)
            report['env_written'] = True
        changed = True
        # Terminal IDs left in a running allowlist are inert. Pruning them on disk alone
        # does not justify restarting Backend. New pending approvals do.
        if not set(target).issubset(running_ids):
            report['health'] = 'UNVERIFIED'
            host.apply()
            report['recreated'] = True
            applied_config = host.config()
            require(set(target).issubset(host.running(applied_config)), 'APPLY_FAILED')
            deadline = monotonic() + HEALTH_SECONDS
            while not host.healthy(applied_config):
                require(monotonic() < deadline, 'HEALTH_FAILED')
                sleep(5)
            report['health'] = 'UP'
        deadline = monotonic() + POLL_SECONDS
        while True:
            rows = host.rows(ids)
            record(rows)
            if not any(row['status'] == 'PENDING' for row in report['events']) or monotonic() >= deadline:
                break
            sleep(5)
        # Availability must also hold after observing publication, including no-recreate runs.
        require(host.healthy(config), 'HEALTH_FAILED')
        report['code'] = ('FAILED' if any(row['status'] in {'FAILED', 'NOT_FOUND'} for row in report['events'])
                          else 'PENDING' if any(row['status'] == 'PENDING' for row in report['events']) else 'PUBLISHED')
    except Exception as failure:
        code = str(failure) if isinstance(failure, Rejected) else 'INTERNAL_ERROR'
        report['code'] = code if code in CODES else 'INTERNAL_ERROR'
        if changed:
            if report['code'] == 'CONFIGURATION_REJECTED':
                report['code'] = 'APPLY_FAILED'
            try:
                record(host.rows(ids))
            except Exception:
                report['events'] = [{'id': value, 'status': 'UNKNOWN'} for value in ids]
        if report['code'] == 'HEALTH_FAILED':
            report['health'] = 'UNVERIFIED'
    return report


def main():
    try:
        require(len(sys.argv) == 1, 'INVALID_INPUT')
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        require(len(raw) <= MAX_INPUT, 'INVALID_INPUT')
        ids = request_ids(raw)
        report = approve(Host(Path(__file__).resolve().parents[1]), ids)
    except Exception as failure:
        code = str(failure) if isinstance(failure, Rejected) else 'INVALID_INPUT'
        report = {'version': 1, 'code': code if code in CODES else 'INVALID_INPUT',
                  'env_written': False, 'recreated': False, 'health': 'UNVERIFIED', 'events': []}
    print(json.dumps(report, separators=(',', ':')))
    return 0 if report['code'] == 'PUBLISHED' else 1


if __name__ == '__main__':
    sys.exit(main())
