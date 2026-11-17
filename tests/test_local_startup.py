"""Local Compose preflight and actual HTTP readiness probe; no Docker daemon required."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / 'compose/compose.yml'
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('bash')


class LocalStartupTests(unittest.TestCase):
    def render(self, missing=None):
        with tempfile.TemporaryDirectory(prefix='m2-local-config-') as directory:
            empty = Path(directory) / 'empty.env'
            empty.write_text('')
            env = {**os.environ, 'POSTGRES_DB': 'local_test', 'POSTGRES_USER': 'local_test',
                   'POSTGRES_PASSWORD': 'local-synthetic', 'TRACKING_CODE_ENCRYPTION_KEY_BASE64': 'test-only',
                   'TRACKING_CODE_ENCRYPTION_KEY_ID': 'test-only', 'MOCK_CITIZEN_PASSWORD': 'test-only',
                   'MOCK_AGENT_PASSWORD': 'test-only', 'MOCK_AREA_RESPONSIBLE_PASSWORD': 'test-only',
                   'MOCK_ADMIN_PASSWORD': 'test-only', 'IDENTITY_PROVIDER': 'MOCK', 'SIMULATOR_ENABLED': 'false',
                   'CORE_OUTBOX_ENABLED': 'false', 'CORE_INBOX_ENABLED': 'false'}
            if missing:
                env[missing] = ''
            return subprocess.run(['docker', 'compose', '--env-file', str(empty), '-f', str(COMPOSE),
                                   'config', '--format', 'json'], env=env, capture_output=True, text=True, timeout=20)

    def test_required_values_fail_with_variable_name_before_startup(self):
        for name in ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD',
                     'TRACKING_CODE_ENCRYPTION_KEY_BASE64', 'MOCK_AGENT_PASSWORD'):
            with self.subTest(variable=name):
                result = self.render(name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_readiness_order_and_isolated_defaults(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)['services']
        self.assertEqual(set(services), {'postgres', 'backend', 'frontend'})
        self.assertEqual(services['backend']['depends_on']['postgres']['condition'], 'service_healthy')
        self.assertEqual(services['frontend']['depends_on']['backend']['condition'], 'service_healthy')
        self.assertEqual(services['backend']['environment']['IDENTITY_PROVIDER'], 'MOCK')
        self.assertEqual(services['backend']['environment']['CORE_INBOX_ENABLED'], 'false')

    def test_health_probe_checks_http_status_not_merely_open_port(self):
        command = json.loads(self.render().stdout)['services']['backend']['healthcheck']['test'][3].replace('$$', '$')
        for status, expected in ((200, 0), (503, 1)):
            with self.subTest(status=status):
                with socket.socket() as server:
                    server.bind(('127.0.0.1', 0))
                    server.listen(1)
                    port = server.getsockname()[1]
                    requests = []
                    def respond():
                        with server.accept()[0] as connection:
                            connection.settimeout(3)
                            request = b''
                            while not request.endswith(b'\r\n\r\n') and len(request) < 8192:
                                fragment = connection.recv(4096)
                                if not fragment:
                                    break
                                request += fragment
                            requests.append(request)
                            connection.sendall(f'HTTP/1.1 {status} Test\r\nContent-Length: 0\r\n\r\n'.encode())
                    worker = threading.Thread(target=respond, daemon=True)
                    worker.start()
                    completed = subprocess.run([BASH, '-ec', command.replace('/8080', '/' + str(port))],
                                               capture_output=True, timeout=5)
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(requests, [b'GET /actuator/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'])
                    self.assertEqual(completed.returncode, expected)


if __name__ == '__main__':
    unittest.main()
