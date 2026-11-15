"""Exercise the SSH forced-command wrapper locally; no network connections."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get('TEST_BASH', shutil.which('bash'))


def shell_path(path):
    text = str(path).replace('\\', '/')
    return '/' + text[0].lower() + text[2:] if os.name == 'nt' and len(text) > 1 and text[1] == ':' else text


@unittest.skipUnless(BASH, 'Bash required')
class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.lock = self.root / 'DA2/.deploy'
        self.lock.mkdir(parents=True)
        self.env = os.environ.copy()
        self.env['HOME'] = shell_path(self.root)
        self.env['TEST_STATE'] = shell_path(self.root)
        self.env['SSH_ORIGINAL_COMMAND'] = 'approve-core-outbox-v1'
        for name, body in {
            'flock': 'printf "%s\\n" "$*" > "$TEST_STATE/flock-call"; exit "${FIXTURE_LOCK_FAIL:-0}"',
            'timeout': 'printf "%s\\n" "$*" > "$TEST_STATE/timeout-call"; printf \'{"fixture":true}\\n\'',
        }.items():
            path = self.bin / name
            path.write_text('#!/usr/bin/env bash\n' + body + '\n', newline='\n')
            path.chmod(0o755)

    def command(self, *args):
        return [BASH, '-c', 'export PATH="$1:$PATH"; shift; exec bash "$@"', 'fixture',
                shell_path(self.bin), shell_path(ROOT / 'scripts/approve-core-outbox.sh'), *args]

    def execute(self, *args):
        return subprocess.run(self.command(*args), env=self.env, text=True, capture_output=True, timeout=10)

    def test_fixed_command_takes_shared_lock_and_bounded_host_runtime(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'flock-call').read_text().strip(), '-w 600 9')
        self.assertTrue((self.lock / 'm2-deploy.lock').is_file())
        self.assertIn('--signal=TERM --kill-after=10 900 python3', (self.root / 'timeout-call').read_text())

    def test_arbitrary_commands_and_arguments_rejected_before_lock(self):
        for command in ('', 'bash', 'approve-core-outbox-v1; id', 'approve-core-outbox-v1 '):
            self.env['SSH_ORIGINAL_COMMAND'] = command
            self.assertEqual(self.execute().returncode, 64)
        self.env['SSH_ORIGINAL_COMMAND'] = 'approve-core-outbox-v1'
        self.assertEqual(self.execute('extra').returncode, 64)
        self.assertFalse((self.root / 'flock-call').exists())
        self.assertFalse((self.root / 'timeout-call').exists())

    def test_lock_timeout_and_both_sentinels_stop_before_host_logic(self):
        self.env['FIXTURE_LOCK_FAIL'] = '1'
        self.assertEqual(self.execute().returncode, 75)
        self.env['FIXTURE_LOCK_FAIL'] = '0'
        for sentinel in ('m2-maintenance', 'm2-outbox-disabled'):
            path = self.lock / sentinel
            path.touch()
            self.assertEqual(self.execute().returncode, 75)
            path.unlink()
        self.assertFalse((self.root / 'timeout-call').exists())

    @unittest.skipUnless(os.name == 'posix' and shutil.which('flock'), 'Real POSIX flock required')
    def test_real_shared_lock_blocks_until_existing_deployment_releases_it(self):
        import fcntl
        (self.bin / 'flock').unlink()  # Use the system flock for this case.
        with (self.lock / 'm2-deploy.lock').open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            process = subprocess.Popen(self.command(), env=self.env, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                time.sleep(0.3)
                self.assertIsNone(process.poll())
                self.assertFalse((self.root / 'timeout-call').exists())
                fcntl.flock(stream, fcntl.LOCK_UN)
                output, error = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, error)
                self.assertIn('fixture', output)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


FAKE_DOCKER = r'''
import json, pathlib, re, sys
root = pathlib.Path(FIXTURE_ROOT)
args = sys.argv[1:]
with (root / 'calls').open('a') as stream: stream.write(json.dumps(args) + '\n')
base = json.loads((root / 'fixture-config.json').read_text())
def selected():
    path = pathlib.Path(args[args.index('--env-file') + 1])
    lines = path.read_text().splitlines()
    approved = next(line.split('=', 1)[1] for line in lines if line.startswith('CORE_OUTBOX_APPROVED_EVENT_IDS='))
    base['services']['backend']['environment']['CORE_OUTBOX_APPROVED_EVENT_IDS'] = approved
    return base
if args[:2] == ['image', 'inspect']: print('sha256:' + 'a' * 64)
elif args[0] == 'inspect':
    env = base['services']['backend']['environment']
    env['CORE_OUTBOX_APPROVED_EVENT_IDS'] = (root / 'running').read_text()
    print(json.dumps([{'State': {'Running': True}, 'Image': 'sha256:' + 'a' * 64, 'Config': {
        'Image': base['services']['backend']['image'], 'Env': [k + '=' + v for k, v in env.items()],
        'Labels': {'com.docker.compose.project': base['name'], 'com.docker.compose.service': 'backend',
                   'com.docker.compose.config-hash': 'f' * 64}}}]))
elif 'config' in args:
    print('backend ' + 'f' * 64 if '--hash' in args else json.dumps(selected()))
elif 'ps' in args: print('d' * 64)
elif 'up' in args:
    (root / 'running').write_text(selected()['services']['backend']['environment']['CORE_OUTBOX_APPROVED_EVENT_IDS'])
elif 'exec' in args:
    sql = sys.stdin.read()
    assert sql.startswith('BEGIN READ ONLY;') and 'payload' not in sql
    for event_id in re.findall("'([0-9a-f-]{36})'::uuid", sql):
        if event_id == '11111111-1111-4111-8111-111111111111':
            status = 'PUBLISHED' if event_id in (root / 'running').read_text() else 'PENDING'
            print(event_id + '|' + status + '|0|' + ('t' if status == 'PUBLISHED' else 'f') + '|ticketCreated')
else: raise SystemExit(90)
'''


@unittest.skipUnless(os.name == 'posix' and BASH and shutil.which('flock'), 'Linux CLI fixtures required')
class RealHostProcessTests(unittest.TestCase):
    def test_actual_wrapper_python_atomic_env_and_readonly_psql_protocol_with_fake_docker(self):
        from test_core_outbox_approval import A, B, SECRET, config
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / 'scripts'; scripts.mkdir()
            compose = root / 'compose'; compose.mkdir()
            binaries = root / 'bin'; binaries.mkdir()
            for name in ('approve-core-outbox.sh', 'core_outbox_approval.py', 'validate-integration-config.py'):
                shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
            (scripts / 'smoke-https.sh').write_text('#!/usr/bin/env bash\nexit 0\n')
            (compose / 'compose.deploy.yml').write_text('services: {}\n')
            runtime = compose / '.env'
            original = b'# private fixture\nUNRELATED=keep\nCORE_OUTBOX_APPROVED_EVENT_IDS=\n'
            runtime.write_bytes(original); runtime.chmod(0o600)
            images = compose / 'images.env'; images.write_text('fixture immutable images\n')
            (root / 'running').write_text('')
            selected = config()
            selected['services']['backend']['environment'].update({
                'IDENTITY_PROVIDER': 'M1', 'SIMULATOR_ENABLED': 'false',
                'CORE_RABBIT_USERNAME': 'fixture', 'CORE_RABBIT_PASSWORD': SECRET,
                'CORE_RABBIT_HOST': 'fixture.example.org', 'CORE_RABBIT_PORT': '5671', 'CORE_RABBIT_VHOST': 'core',
                'APP_JWT_SECRET': 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=',
                'M1_BASE_URL': 'https://identity.example.org', 'M2_AUTH_BROWSER_ORIGIN': 'https://fixture.example.org',
                'M2_PUBLIC_BASE_URL': 'https://fixture.example.org',
            })
            (root / 'fixture-config.json').write_text(json.dumps(selected))
            docker = binaries / 'docker'
            docker.write_text('#!/usr/bin/env python3\nFIXTURE_ROOT=' + repr(str(root)) + '\n' + FAKE_DOCKER)
            docker.chmod(0o755)
            curl = binaries / 'curl'; curl.write_text('#!/bin/sh\nprintf \'{"status":"UP"}\\n\'\n'); curl.chmod(0o755)
            env = os.environ.copy()
            env.update(HOME=str(root), PATH=str(binaries) + os.pathsep + env['PATH'], SSH_ORIGINAL_COMMAND='approve-core-outbox-v1')
            def invoke(ids):
                return subprocess.run([BASH, str(scripts / 'approve-core-outbox.sh')],
                                      input=json.dumps({'event_ids': ids}), env=env,
                                      capture_output=True, text=True, timeout=20)
            rejected = invoke([A, B])
            self.assertEqual(rejected.returncode, 1, rejected.stderr)
            self.assertEqual(json.loads(rejected.stdout)['code'], 'BATCH_REJECTED')
            self.assertEqual(runtime.read_bytes(), original)
            first = invoke([A])
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(json.loads(first.stdout)['code'], 'PUBLISHED')
            self.assertEqual(runtime.read_bytes(), original.replace(b'CORE_OUTBOX_APPROVED_EVENT_IDS=\n',
                                                                   ('CORE_OUTBOX_APPROVED_EVENT_IDS=' + A + '\n').encode()))
            second = invoke([A])
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertFalse(json.loads(second.stdout)['recreated'])
            calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
            up = [args for args in calls if 'up' in args]
            self.assertEqual(len(up), 1)
            self.assertEqual(up[0][-7:], ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'backend'])
            self.assertEqual(images.read_text(), 'fixture immutable images\n')
            self.assertNotIn(SECRET, first.stdout + first.stderr + second.stdout + second.stderr)
            self.assertEqual(list(compose.glob('.env.outbox.*')), [])


if __name__ == '__main__':
    unittest.main()
