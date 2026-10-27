"""Deterministic failure-path tests; mocks do NOT prove live TLS or ACME issuance."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get('TEST_BASH', shutil.which('bash'))


def shell_path(path):
    value = str(path).replace('\\', '/')
    if os.name == 'nt' and len(value) > 1 and value[1] == ':':
        value = '/' + value[0].lower() + value[2:]
    return value


FAKE_TOOL = r'''
import json, os, pathlib, sys
name, *args = sys.argv[1:]
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
phase = (root / 'phase').read_text() if (root / 'phase').exists() else 'preflight'
if name == 'docker':
    with (root / 'docker.log').open('a') as log:
        log.write(' '.join(args) + '\n')
    if 'config' in args:
        print(json.dumps({'services': {'caddy': {'environment': {
            'HTTPS_HOSTNAME': 'test.example.com', 'LEGACY_HTTP_IP': '54.232.233.237'
        }}}}))
    if 'up' in args:
        selected = args[args.index('--env-file', args.index('--env-file') + 1) + 1]
        (root / 'phase').write_text('deployment' if '.candidate.' in selected else 'rollback')
    if args[:2] == ['image', 'inspect'] and os.environ.get('CACHE_MISS'):
        raise SystemExit(1)
    if 'pull' in args and phase != 'preflight' and os.environ.get('ROLLBACK_PULL_FAIL'):
        raise SystemExit(1)
    raise SystemExit(0)
if name in ('flock', 'sleep'):
    raise SystemExit(0)
fault = os.environ.get('SMOKE_FAULT', '')
if os.environ.get('DEPLOY_FAULT') and phase == 'deployment':
    fault = 'tls'
if os.environ.get('DEPLOY_FAULT') == 'rollback' and phase == 'rollback':
    fault = 'tls'
url = args[-1]
status, body, content_type, location = 200, '<html>React</html>', 'text/html', None
if 'actuator/health' in url:
    body = os.environ.get('HEALTH_BODY', '{"status":"UP"}')
    content_type = os.environ.get('HEALTH_CONTENT_TYPE', 'application/json')
    status = int(os.environ.get('HEALTH_HTTP_STATUS', '200'))
    if fault == 'health': body = '{"status":"DOWN"}'
elif url.startswith('https:'):
    if fault == 'tls': raise SystemExit(60)
    if '/api?' in url:
        status, location = 308, '/api/?probe=https-smoke&n=1'
        if fault == 'badredirect': location = 'http://test.example.com/api/'
    elif '/api/catalog' in url:
        body, content_type = '[]', 'application/json'
        if fault == 'spa': body, content_type = '<html>SPA</html>', 'text/html'
        if fault == 'api_redirect': status, location = 308, 'https://test.example.com/'
        if fault == 'invalid_json': body = '{}'
    elif fault == 'empty_html': body = ''
else:
    status = 308
    location = 'https://test.example.com/api/catalog/categories?probe=https-smoke&n=1'
    if fault == 'redirect200': status = 200
    if fault == 'wrong_host': location = location.replace('test.example.com', 'attacker.invalid')
headers = f'HTTP/1.1 {status} Test\r\nContent-Type: {content_type}\r\n'
if location: headers += f'Location: {location}\r\n'
pathlib.Path(args[args.index('--dump-header') + 1]).write_text(headers + '\r\n')
pathlib.Path(args[args.index('--output') + 1]).write_text(body)
print(status, end='')
'''


class HttpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('bin', 'scripts', 'compose', 'state'):
            (self.root / directory).mkdir()
        self.env = os.environ.copy()
        self.env['FIXTURE_ROOT'] = str(self.root)
        self.env['PATH'] = str(self.root / 'bin') + os.pathsep + self.env['PATH']
        self.env['PYTHONUTF8'] = '1'
        for name in ('docker', 'curl', 'flock', 'sleep'):
            file = self.root / 'bin' / name
            file.write_text(f'#!/usr/bin/env bash\nexec python3 "{shell_path(self.root / "tools.py")}" {name} "$@"\n')
            file.chmod(0o755)
        python = self.root / 'bin' / 'python3'
        python.write_text(f'#!/usr/bin/env bash\nexec "{shell_path(sys.executable)}" "$@"\n')
        python.chmod(0o755)
        (self.root / 'tools.py').write_text(FAKE_TOOL)
        shutil.copyfile(ROOT / 'scripts/smoke-https.sh', self.root / 'scripts/smoke-https.sh')
        # Only isolate the production lock's directory; do not touch the user's HOME.
        deploy = (ROOT / 'scripts/deploy-service.sh').read_text()
        lock_line = 'lock_dir="$HOME/DA2/.deploy"'
        self.assertIn(lock_line, deploy)
        deploy = deploy.replace(lock_line, f'lock_dir="{shell_path(self.root / "state")}"')
        (self.root / 'scripts/deploy-service.sh').write_text(deploy, newline='\n')
        (self.root / 'compose/compose.deploy.yml').write_text('services: {}\n')
        (self.root / 'compose/.env').write_text('# private test fixture\n')
        self.images = self.root / 'compose/images.env'
        self.original = ('BACKEND_IMAGE=ghcr.io/juanmaguida/backend-damii:sha-' + 'a' * 40 + '\n'
                         'FRONTEND_IMAGE=ghcr.io/santimussi/front-desarrollo-apps-2:sha-' + 'b' * 40 + '\n')
        self.images.write_text(self.original)

    def run_script(self, name, *args):
        # Git Bash prepends its own tools on startup; prepend mocks after startup.
        return subprocess.run([BASH, '-c', 'export PATH="$1:$PATH"; shift; exec bash "$@"',
                               'fixture', shell_path(self.root / 'bin'),
                               shell_path(self.root / 'scripts' / name), *args],
                              env=self.env, capture_output=True, text=True, timeout=180)

    def test_smoke_accepts_expected_responses(self):
        result = self.run_script('smoke-https.sh', 'test.example.com')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_health_accepts_json_media_types_and_real_actuator_body(self):
        self.env['HEALTH_BODY'] = '{"groups":["liveness","readiness"],"status":"UP"}'
        for content_type in ('application/json',
                             'application/vnd.spring-boot.actuator.v3+json',
                             'Application/Vnd.Spring-Boot.Actuator.V3+Json; charset=UTF-8',
                             'application/problem+json'):
            with self.subTest(content_type=content_type):
                self.env['HEALTH_CONTENT_TYPE'] = content_type
                result = self.run_script('smoke-https.sh', 'test.example.com')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('HTTPS smoke passed:', result.stdout)

    def test_health_rejects_invalid_responses(self):
        valid = {'HEALTH_HTTP_STATUS': '200',
                 'HEALTH_CONTENT_TYPE': 'application/vnd.spring-boot.actuator.v3+json',
                 'HEALTH_BODY': '{"groups":["liveness","readiness"],"status":"UP"}'}
        invalid = [
            {'HEALTH_CONTENT_TYPE': value}
            for value in ('text/html', 'text/plain', 'text/json', 'application/jsonp',
                          'application/json-invalid', 'application/+json',
                          'application/vnd.spring-boot.actuator.v3+json-invalid',
                          'text/plain; note=application/json', '')
        ] + [
            {'HEALTH_BODY': value}
            for value in ('{"status":"DOWN"}', '{"status":"UNKNOWN"}', '{"status":"up"}',
                          '{"groups":["liveness","readiness"]}', 'not JSON',
                          '<html>UP</html>', '[]', 'null')
        ] + [{'HEALTH_HTTP_STATUS': value} for value in ('201', '204', '301', '503')]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                self.env.update(valid)
                self.env.update(overrides)
                result = self.run_script('smoke-https.sh', 'test.example.com')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('HTTPS smoke passed:', result.stdout)

    def test_smoke_rejects_false_positives(self):
        for fault in ('tls', 'spa', 'api_redirect', 'invalid_json', 'health',
                      'badredirect', 'redirect200', 'wrong_host', 'empty_html'):
            with self.subTest(fault=fault):
                self.env['SMOKE_FAULT'] = fault
                result = self.run_script('smoke-https.sh', 'test.example.com')
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_invalid_hostname_rejected_before_network(self):
        for host in ('https://test.example.com', 'test.example.com/path', '127.0.0.1', '-bad.example.com'):
            with self.subTest(host=host):
                self.assertNotEqual(self.run_script('smoke-https.sh', host).returncode, 0)

    def test_maintenance_prevents_mutations(self):
        (self.root / 'state/m2-maintenance').touch()
        result = self.run_script('deploy-service.sh', 'backend', 'c' * 40)
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertFalse((self.root / 'docker.log').exists())

    def test_failed_preflight_preserves_images(self):
        self.env['SMOKE_FAULT'] = 'tls'
        result = self.run_script('deploy-service.sh', 'backend', 'c' * 40)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.images.read_text(), self.original)
        self.assertNotIn(' up ', (self.root / 'docker.log').read_text())

    def test_success_updates_only_selected_sha(self):
        for service, old in (('backend', 'a'), ('frontend', 'b')):
            with self.subTest(service=service):
                self.images.write_text(self.original)
                (self.root / 'phase').write_text('preflight')
                result = self.run_script('deploy-service.sh', service, 'c' * 40)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.images.read_text(), self.original.replace(old * 40, 'c' * 40))
                self.assertNotIn('postgres', (self.root / 'docker.log').read_text())

    def test_failed_deploy_rolls_back_and_verifies_https(self):
        for service in ('backend', 'frontend'):
            with self.subTest(service=service):
                (self.root / 'phase').write_text('preflight')
                self.env['DEPLOY_FAULT'] = 'deployment'
                result = self.run_script('deploy-service.sh', service, 'c' * 40)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('Rollback smoke tests passed', result.stdout)
                self.assertEqual(self.images.read_text(), self.original)
                self.assertEqual(list((self.root / 'compose').glob('.images.env.candidate.*')), [])

    def test_failed_rollback_requires_manual_intervention(self):
        self.env['DEPLOY_FAULT'] = 'rollback'
        result = self.run_script('deploy-service.sh', 'frontend', 'c' * 40)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.images.read_text(), self.original)

    def test_cached_previous_image_can_rollback_while_registry_is_unavailable(self):
        self.env['DEPLOY_FAULT'] = 'deployment'
        self.env['ROLLBACK_PULL_FAIL'] = '1'
        result = self.run_script('deploy-service.sh', 'frontend', 'c' * 40)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Rollback smoke tests passed', result.stdout)
        self.assertEqual(self.images.read_text(), self.original)

    def test_missing_previous_image_and_registry_failure_require_intervention(self):
        self.env['DEPLOY_FAULT'] = 'deployment'
        self.env['ROLLBACK_PULL_FAIL'] = '1'
        self.env['CACHE_MISS'] = '1'
        result = self.run_script('deploy-service.sh', 'backend', 'c' * 40)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.images.read_text(), self.original)


if __name__ == '__main__':
    unittest.main()
