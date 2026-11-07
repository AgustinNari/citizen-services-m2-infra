"""Render Compose with synthetic values only; no containers, network or private .env."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEST_KEY = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='


@unittest.skipUnless(shutil.which('docker'), 'Docker CLI is required to render Compose')
class TrackingConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # WSL may expose a Docker Desktop shim even when its CLI integration is absent.
        # CI separately requires successful Compose rendering before running this suite.
        result = subprocess.run(['docker', 'compose', 'version', '--short'],
                                capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise unittest.SkipTest('A runnable Docker Compose CLI is required (not an unavailable WSL shim)')

    def render(self, filename, missing=None):
        env = os.environ.copy()
        env.update(POSTGRES_DB='tracking_test', POSTGRES_USER='tracking_test',
                   POSTGRES_PASSWORD='synthetic-test-password',
                   MOCK_CITIZEN_PASSWORD='synthetic-citizen', MOCK_AGENT_PASSWORD='synthetic-agent',
                   MOCK_AREA_RESPONSIBLE_PASSWORD='synthetic-area', MOCK_ADMIN_PASSWORD='synthetic-admin',
                   HTTPS_HOSTNAME='atencion-ciudadana.ddnsfree.com',
                   TRACKING_CODE_ENCRYPTION_KEY_BASE64=TEST_KEY,
                   TRACKING_CODE_ENCRYPTION_KEY_ID='ci-v1',
                   BACKEND_IMAGE='example.invalid/backend:test', FRONTEND_IMAGE='example.invalid/frontend:test')
        if missing:
            env.pop(missing, None)
        return subprocess.run(['docker', 'compose', '--env-file', str(ROOT / 'compose/.env.example'),
                               '-f', str(ROOT / 'compose' / filename), 'config', '--format', 'json'],
                              cwd=ROOT / 'compose', env=env, capture_output=True, text=True, timeout=30)

    def test_both_compose_files_forward_explicit_key_only_to_backend(self):
        for filename in ('compose.yml', 'compose.deploy.yml'):
            with self.subTest(compose=filename):
                result = self.render(filename)
                self.assertEqual(result.returncode, 0, result.stderr)
                services = json.loads(result.stdout)['services']
                self.assertEqual(services['backend']['environment']['TRACKING_CODE_ENCRYPTION_KEY_BASE64'], TEST_KEY)
                self.assertEqual(services['backend']['environment']['TRACKING_CODE_ENCRYPTION_KEY_ID'], 'ci-v1')
                for name, service in services.items():
                    if name != 'backend':
                        self.assertNotIn('TRACKING_CODE_ENCRYPTION_KEY_BASE64', service.get('environment', {}))
                    self.assertNotIn('TRACKING_CODE_ENCRYPTION_KEY_BASE64', service.get('build', {}).get('args', {}))

    def test_missing_key_or_id_fails_before_starting_any_container(self):
        for filename in ('compose.yml', 'compose.deploy.yml'):
            for missing in ('TRACKING_CODE_ENCRYPTION_KEY_BASE64', 'TRACKING_CODE_ENCRYPTION_KEY_ID'):
                with self.subTest(compose=filename, missing=missing):
                    result = self.render(filename, missing)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(missing, result.stderr)


if __name__ == '__main__':
    unittest.main()
