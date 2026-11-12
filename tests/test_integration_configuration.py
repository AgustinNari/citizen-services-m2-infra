"""Offline policy and Compose interpolation tests. All credential values are synthetic."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/validate-integration-config.py'
SPEC = importlib.util.spec_from_file_location('integration_config', SCRIPT)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
KEY = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='
HOST = 'tickets.example.test'
EVENT = '293c56b5-a2ee-409b-8ab7-107cff88df7e'


def configuration(**overrides):
    env = dict(IDENTITY_PROVIDER='M1', APP_JWT_SECRET=KEY,
               M1_BASE_URL='https://ciudadanos-m1-backend.onrender.com',
               M2_AUTH_BROWSER_ORIGIN='https://' + HOST, M2_PUBLIC_BASE_URL='https://' + HOST,
               CORE_OUTBOX_ENABLED='false', CORE_INBOX_ENABLED='false',
               CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS='false', CORE_OUTBOX_APPROVED_EVENT_IDS='',
               CORE_RABBIT_HOST='rabbit.example.test', CORE_RABBIT_PORT='5671', CORE_RABBIT_VHOST='core',
               CORE_RABBIT_USERNAME='synthetic-user', CORE_RABBIT_PASSWORD='synthetic-private-password',
               SIMULATOR_ENABLED='false')
    env.update(overrides)
    return {'services': {'backend': {'environment': env}, 'frontend': {'image': 'example.invalid/frontend:test'},
                         'caddy': {'environment': {'HTTPS_HOSTNAME': HOST, 'LEGACY_HTTP_IP': '192.0.2.1'}}}}


class IntegrationPolicyTests(unittest.TestCase):
    def test_mock_without_any_new_secret_and_each_deliberate_real_stage_are_valid(self):
        for config in (configuration(IDENTITY_PROVIDER='MOCK', APP_JWT_SECRET='', CORE_RABBIT_USERNAME='', CORE_RABBIT_PASSWORD=''),
                       configuration(), configuration(CORE_INBOX_ENABLED='true'),
                       configuration(CORE_OUTBOX_ENABLED='true', CORE_OUTBOX_APPROVED_EVENT_IDS=EVENT),
                       configuration(CORE_OUTBOX_ENABLED='true', CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS='true'),
                       configuration(CORE_OUTBOX_ENABLED='true', CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS='true', CORE_INBOX_ENABLED='true')):
            with self.subTest(config_mode=config['services']['backend']['environment']['IDENTITY_PROVIDER']):
                self.assertEqual(policy.validate(config), (HOST, '192.0.2.1'))

    def test_invalid_modes_secrets_origins_and_tls_targets_fail_closed(self):
        cases = [dict(IDENTITY_PROVIDER='unknown'), dict(APP_JWT_SECRET=''), dict(APP_JWT_SECRET='not-base64'),
                 dict(APP_JWT_SECRET='YQ=='), dict(M1_BASE_URL='http://ciudadanos-m1-backend.onrender.com'),
                 dict(M1_BASE_URL='https://user:password@example.test'), dict(M2_AUTH_BROWSER_ORIGIN='http://' + HOST),
                 dict(M2_AUTH_BROWSER_ORIGIN='https://other.example.test'), dict(M2_PUBLIC_BASE_URL='https://other.example.test'),
                 dict(CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS='true'),
                 dict(CORE_OUTBOX_ENABLED='true', CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS='true', CORE_OUTBOX_APPROVED_EVENT_IDS=EVENT),
                 dict(CORE_INBOX_ENABLED='true', IDENTITY_PROVIDER='MOCK'), dict(CORE_INBOX_ENABLED='true', SIMULATOR_ENABLED='true'),
                 dict(CORE_INBOX_ENABLED='true', CORE_RABBIT_PASSWORD=''), dict(CORE_INBOX_ENABLED='true', CORE_RABBIT_PORT='5672'),
                 dict(CORE_INBOX_ENABLED='true', CORE_RABBIT_VHOST='/'), dict(CORE_INBOX_ENABLED='true', CORE_RABBIT_HOST='amqps://rabbit.test'),
                 dict(CORE_OUTBOX_ENABLED='true', CORE_OUTBOX_APPROVED_EVENT_IDS='*'),
                 dict(CORE_INBOX_ENABLED='true', CORE_OUTBOX_APPROVED_EVENT_IDS='*'), dict(CORE_INBOX_ENABLED='yes')]
        for values in cases:
            with self.subTest(fields=sorted(values)):
                with self.assertRaises((ValueError, KeyError)):
                    policy.validate(configuration(**values))

    def test_cli_never_echoes_rejected_credentials_or_json(self):
        secret = 'synthetic-secret-that-must-not-be-printed'
        for body in (json.dumps(configuration(APP_JWT_SECRET=secret)), '{"secret":"' + secret):
            result = subprocess.run([sys.executable, str(SCRIPT)], input=body, capture_output=True, text=True)
            self.assertEqual(result.returncode, 78)
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertNotIn(KEY, result.stdout + result.stderr)

    def test_private_keys_cannot_be_forwarded_to_frontend_or_build(self):
        for where in ('environment', 'build'):
            config = configuration()
            config['services']['frontend'][where] = {'APP_JWT_SECRET': KEY} if where == 'environment' else {'args': {'APP_JWT_SECRET': KEY}}
            with self.assertRaises(ValueError):
                policy.validate(config)


@unittest.skipUnless(shutil.which('docker'), 'Docker Compose CLI required')
class IntegrationComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['docker', 'compose', 'version', '--short'], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise unittest.SkipTest('Runnable Docker Compose CLI required')

    def render(self, filename, **overrides):
        env = os.environ.copy()
        env.update(POSTGRES_DB='synthetic', POSTGRES_USER='synthetic', POSTGRES_PASSWORD='synthetic-password',
                   TRACKING_CODE_ENCRYPTION_KEY_BASE64=KEY, TRACKING_CODE_ENCRYPTION_KEY_ID='test-only',
                   MOCK_CITIZEN_PASSWORD='synthetic', MOCK_AGENT_PASSWORD='synthetic', MOCK_AREA_RESPONSIBLE_PASSWORD='synthetic',
                   MOCK_ADMIN_PASSWORD='synthetic', HTTPS_HOSTNAME=HOST, LEGACY_HTTP_IP='192.0.2.1',
                   IDENTITY_PROVIDER='MOCK', APP_JWT_SECRET='', VITE_IDENTITY_PROVIDER='MOCK', SIMULATOR_ENABLED='false',
                   M2_AUTH_BROWSER_ORIGIN='https://' + HOST, M2_PUBLIC_BASE_URL='https://' + HOST,
                   CORE_OUTBOX_ENABLED='false', CORE_INBOX_ENABLED='false', CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS='false',
                   CORE_OUTBOX_APPROVED_EVENT_IDS='', CORE_RABBIT_USERNAME='', CORE_RABBIT_PASSWORD='',
                   BACKEND_IMAGE='example.invalid/backend:test', FRONTEND_IMAGE='example.invalid/frontend:test')
        env.update(overrides)
        result = subprocess.run(['docker', 'compose', '--project-name', 'integration-validation',
                                 '--env-file', str(ROOT / 'compose/.env.example'), '-f', str(ROOT / 'compose' / filename),
                                 'config', '--format', 'json'], cwd=ROOT / 'compose', env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, 'Compose interpolation failed with synthetic inputs')
        return json.loads(result.stdout)

    def test_defaults_keep_mock_and_core_off_and_preserve_profile_and_volumes(self):
        for filename in ('compose.yml', 'compose.deploy.yml'):
            config = self.render(filename)
            policy.validate(config)
            env = config['services']['backend']['environment']
            self.assertEqual(env['IDENTITY_PROVIDER'], 'MOCK')
            self.assertEqual(env['SPRING_PROFILES_ACTIVE'], 'dev')
            for key in ('CORE_OUTBOX_ENABLED', 'CORE_INBOX_ENABLED', 'CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS'):
                self.assertEqual(env[key], 'false')
            self.assertIn('postgres_data', config['volumes'])
            self.assertIn('attachment_data', config['volumes'])

    def test_m1_and_independent_manual_automatic_and_consumer_settings_reach_only_backend(self):
        for filename in ('compose.yml', 'compose.deploy.yml'):
            for selected in ({}, {'CORE_OUTBOX_ENABLED': 'true', 'CORE_OUTBOX_APPROVED_EVENT_IDS': EVENT},
                             {'CORE_OUTBOX_ENABLED': 'true', 'CORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS': 'true'}, {'CORE_INBOX_ENABLED': 'true'}):
                config = self.render(filename, IDENTITY_PROVIDER='M1', VITE_IDENTITY_PROVIDER='M1', APP_JWT_SECRET=KEY,
                                     CORE_RABBIT_USERNAME='synthetic-user', CORE_RABBIT_PASSWORD='synthetic-private-password', **selected)
                policy.validate(config)
                env = config['services']['backend']['environment']
                self.assertEqual(env['APP_JWT_SECRET'], KEY)
                self.assertEqual(env['M1_BASE_URL'], 'https://ciudadanos-m1-backend.onrender.com')
                self.assertEqual(env['M2_AUTH_BROWSER_ORIGIN'], 'https://' + HOST)
                for key, value in selected.items():
                    self.assertEqual(env[key], value)
                for name, service in config['services'].items():
                    if name != 'backend':
                        self.assertNotIn(KEY, json.dumps(service))
                        self.assertNotIn('synthetic-private-password', json.dumps(service))

    def test_local_build_uses_vite_arguments_and_deploy_uses_prebuilt_image(self):
        local = self.render('compose.yml', IDENTITY_PROVIDER='M1', APP_JWT_SECRET=KEY, VITE_IDENTITY_PROVIDER='M1')
        args = local['services']['frontend']['build']['args']
        self.assertEqual(args, {'VITE_API_BASE_URL': '/api', 'VITE_IDENTITY_PROVIDER': 'M1'})
        deploy = self.render('compose.deploy.yml')
        self.assertNotIn('build', deploy['services']['frontend'])
        self.assertNotIn('environment', deploy['services']['frontend'])

    def test_deploy_derives_origin_from_current_hostname_without_mutating_proxy_architecture(self):
        config = self.render('compose.deploy.yml', HTTPS_HOSTNAME='changed.example.test')
        env = config['services']['backend']['environment']
        self.assertEqual(env['M2_AUTH_BROWSER_ORIGIN'], 'https://changed.example.test')
        self.assertEqual(env['M2_PUBLIC_BASE_URL'], env['M2_AUTH_BROWSER_ORIGIN'])
        self.assertFalse(config['services']['frontend'].get('ports'))
        self.assertFalse(config['services']['postgres'].get('ports'))


if __name__ == '__main__':
    unittest.main()
