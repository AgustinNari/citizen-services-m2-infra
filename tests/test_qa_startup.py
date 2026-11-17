"""G1 configuration boundaries; real runtime/AMQPS checks live in VerifyG1.java."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'compose/qa'
REQUIRED = ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD',
            'TRACKING_CODE_ENCRYPTION_KEY_BASE64', 'TRACKING_CODE_ENCRYPTION_KEY_ID',
            'MOCK_CITIZEN_PASSWORD', 'MOCK_AGENT_PASSWORD', 'MOCK_AREA_RESPONSIBLE_PASSWORD',
            'MOCK_ADMIN_PASSWORD', 'QA_RABBIT_USER', 'QA_RABBIT_PASSWORD', 'QA_TRUSTSTORE_PASSWORD')


class QaStartupTests(unittest.TestCase):
    def render(self, missing=None):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / 'empty.env'
            empty.write_text('')
            env = {**os.environ, **{key: 'synthetic-local-only' for key in REQUIRED},
                   'IDENTITY_PROVIDER': 'M1', 'CORE_RABBIT_HOST': 'shared.invalid',
                   'AI_ENABLED': 'true', 'SIMULATOR_ENABLED': 'true'}
            if missing:
                env[missing] = ''
            return subprocess.run(['docker', 'compose', '--env-file', str(empty), '-f', str(QA / 'compose.yml'),
                                   'config', '--format', 'json'], env=env, capture_output=True, text=True, timeout=20)

    def config(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_each_missing_required_variable_fails_before_startup_with_its_name(self):
        for key in REQUIRED:
            with self.subTest(variable=key):
                result = self.render(key)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)
                self.assertNotIn('synthetic-local-only', result.stderr)

    def test_external_provider_overrides_cannot_escape_local_qa(self):
        config = self.config()
        self.assertTrue(config['networks']['default']['internal'])
        services = config['services']
        self.assertEqual(set(services), {'certificates', 'postgres', 'rabbitmq', 'core-mock', 'backend', 'frontend'})
        env = services['backend']['environment']
        for key, expected in {'IDENTITY_PROVIDER': 'MOCK', 'M1_BASE_URL': 'https://m1.invalid',
                              'SPRING_PROFILES_ACTIVE': 'dev,qa-isolated', 'CORE_OUTBOX_LOCAL_QA': 'true',
                              'CORE_RABBIT_HOST': 'rabbitmq', 'CORE_RABBIT_PORT': '5671',
                              'CORE_INBOX_ENABLED': 'true', 'CORE_OUTBOX_ENABLED': 'true',
                              'AI_ENABLED': 'false', 'SIMULATOR_ENABLED': 'false'}.items():
            self.assertEqual(env[key], expected)
        self.assertEqual(services['frontend']['build']['args']['VITE_IDENTITY_PROVIDER'], 'MOCK')
        self.assertEqual(services['certificates']['network_mode'], 'none')
        for service in services.values():
            for port in service.get('ports', []):
                self.assertEqual(port['host_ip'], '127.0.0.1')
        self.assertNotIn('ports', services['postgres'])
        self.assertNotIn('ports', services['rabbitmq'])
        for name in ('backend', 'core-mock', 'postgres', 'rabbitmq'):
            self.assertNotIn('edge', services[name].get('networks', {}))
            self.assertNotIn('ports', services[name])
        self.assertIn('edge', services['frontend']['networks'])

    def test_ready_order_and_build_paths_use_existing_sources(self):
        services = self.config()['services']
        for name, parent, condition in [('rabbitmq', 'certificates', 'service_completed_successfully'),
                                        ('core-mock', 'rabbitmq', 'service_healthy'),
                                        ('backend', 'rabbitmq', 'service_healthy'),
                                        ('backend', 'postgres', 'service_healthy'),
                                        ('frontend', 'backend', 'service_healthy')]:
            self.assertEqual(services[name]['depends_on'][parent]['condition'], condition)
        # Infra CI checks out this repository alone. Verify the portable layout here;
        # Dockerfile existence is checked by the real three-repository Compose build.
        layout = ROOT.parent.parent
        for name, relative in {'backend': 'Backend/Backend-DAMII',
                               'frontend': 'Frontend/front-desarrollo-apps-2/Front/frontend-atencion-ciudadana'}.items():
            self.assertEqual(Path(services[name]['build']['context']).resolve(), (layout / relative).resolve())
        self.assertIn('-Djavax.net.ssl.trustStore=', ' '.join(services['backend']['entrypoint']))
        self.assertEqual(Path(services['core-mock']['build']['additional_contexts']['qa']), QA)


if __name__ == '__main__':
    unittest.main()
