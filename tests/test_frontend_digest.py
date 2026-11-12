"""Exercise the real deploy script with fake Docker/curl; never reach GHCR or AWS."""
import json
import unittest
import test_https as fixtures

REPO = 'ghcr.io/santimussi/front-desarrollo-apps-2'
DIGEST = REPO + '@sha256:' + '1' * 64
PREVIOUS_DIGEST = REPO + '@sha256:' + '2' * 64
NEW_SHA = REPO + ':sha-' + 'c' * 40
OLD_SHA = REPO + ':sha-' + 'b' * 40


class FrontendDigestTests(unittest.TestCase):
    setUp = fixtures.HttpsTests.setUp
    run_script = fixtures.HttpsTests.run_script

    def m1(self):
        self.env['FIXTURE_BACKEND_ENV_JSON'] = json.dumps({
            'IDENTITY_PROVIDER': 'M1', 'APP_JWT_SECRET': 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=',
            'M1_BASE_URL': 'https://ciudadanos-m1-backend.onrender.com',
            'M2_AUTH_BROWSER_ORIGIN': 'https://test.example.com', 'M2_PUBLIC_BASE_URL': 'https://test.example.com'})
        self.env['FIXTURE_RUNNING_BACKEND_PROVIDER'] = 'M1'

    def providers(self, **values):
        self.env['FIXTURE_IMAGE_PROVIDERS'] = json.dumps(values)

    def calls(self):
        return (self.root / 'docker.log').read_text()

    def up_images(self):
        path = self.root / 'up-images.log'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def unchanged(self, original=None):
        self.assertEqual(self.images.read_text(), original or self.original)
        self.assertEqual((self.root / 'compose/.env').read_text(), '# private test fixture\n')
        self.assertEqual(list((self.root / 'compose').glob('.images.env.candidate.*')), [])
        self.assertNotIn(' up -d --no-deps backend', self.calls())
        self.assertNotIn('postgres', self.calls())

    def test_digest_selection_updates_only_frontend_and_checks_real_backend_and_image_metadata(self):
        self.m1(); self.providers(**{DIGEST: 'M1'})
        result = self.run_script('deploy-service.sh', 'frontend', DIGEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images.read_text(), self.original.replace(OLD_SHA, DIGEST))
        self.assertEqual(self.up_images(), [{'service': 'frontend', 'reference': DIGEST}])
        self.assertIn('ar.edu.uade.m2.identity-provider', self.calls())
        self.assertIn('IDENTITY_PROVIDER', self.calls())
        self.assertNotIn(' up -d --no-deps backend', self.calls())
        self.assertNotIn('postgres', self.calls())

    def test_legacy_sha_can_replace_a_digest_without_changing_backend(self):
        self.images.write_text(self.original.replace(OLD_SHA, PREVIOUS_DIGEST))
        self.providers(**{NEW_SHA: 'MOCK', PREVIOUS_DIGEST: 'MOCK'})
        result = self.run_script('deploy-service.sh', 'frontend', 'c' * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images.read_text(), self.original.replace(OLD_SHA, NEW_SHA))

    def test_invalid_or_mutable_references_are_rejected_before_any_docker_call(self):
        for selection in (REPO + ':latest', REPO + ':dev', REPO + ':m1-sha-' + 'a' * 40 + '-run-1-1',
                          DIGEST.replace('santimussi', 'other'), DIGEST.replace('ghcr.io', 'ghcrXio'),
                          DIGEST.replace('sha256:', 'sha512:'), DIGEST[:-1], DIGEST + 'f',
                          REPO + '@sha256:' + 'A' * 64, REPO + ':dev@sha256:' + '1' * 64,
                          DIGEST + '\n', 'https://' + DIGEST, 'c' * 39):
            with self.subTest(reference=selection):
                result = self.run_script('deploy-service.sh', 'frontend', selection)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertFalse((self.root / 'docker.log').exists())
                self.assertEqual(self.images.read_text(), self.original)
        result = self.run_script('deploy-service.sh', 'backend', DIGEST)
        self.assertEqual(result.returncode, 64)
        self.assertFalse((self.root / 'docker.log').exists())

    def test_digest_without_valid_matching_provider_is_rejected_without_recreation(self):
        self.m1()
        for provider in ('', 'MOCK', 'untrusted', 'm1'):
            with self.subTest(provider=provider):
                self.providers(**{DIGEST: provider})
                result = self.run_script('deploy-service.sh', 'frontend', DIGEST)
                self.assertEqual(result.returncode, 65, result.stderr)
                self.assertEqual(self.up_images(), [])
                self.unchanged()

    def test_m1_image_cannot_be_started_with_mock_backend(self):
        self.providers(**{DIGEST: 'M1', NEW_SHA: 'M1'})
        for selection in (DIGEST, 'c' * 40):
            result = self.run_script('deploy-service.sh', 'frontend', selection)
            self.assertEqual(result.returncode, 65, result.stderr)
            self.assertEqual(self.up_images(), []); self.unchanged()

    def test_mock_image_and_unlabelled_sha_cannot_be_started_with_m1_backend(self):
        self.m1()
        for provider in ('MOCK', ''):
            self.providers(**{NEW_SHA: provider})
            result = self.run_script('deploy-service.sh', 'frontend', 'c' * 40)
            self.assertEqual(result.returncode, 65, result.stderr)
            self.assertEqual(self.up_images(), []); self.unchanged()

    def test_edited_env_does_not_substitute_for_recreating_backend_with_matching_mode(self):
        self.m1(); self.env['FIXTURE_RUNNING_BACKEND_PROVIDER'] = 'MOCK'
        self.providers(**{DIGEST: 'M1'})
        result = self.run_script('deploy-service.sh', 'frontend', DIGEST)
        self.assertEqual(result.returncode, 65, result.stderr)
        self.assertNotIn(' pull ', self.calls()); self.assertEqual(self.up_images(), []); self.unchanged()

    def test_missing_stopped_or_unknown_backend_blocks_frontend_before_pull(self):
        for key, value in (('FIXTURE_BACKEND_MISSING', '1'), ('FIXTURE_BACKEND_RUNNING', 'false'),
                           ('FIXTURE_RUNNING_BACKEND_PROVIDER', 'unknown')):
            with self.subTest(key=key):
                self.env[key] = value
                result = self.run_script('deploy-service.sh', 'frontend', 'c' * 40)
                self.assertEqual(result.returncode, 65, result.stderr)
                self.assertNotIn(' pull ', self.calls()); self.assertEqual(self.up_images(), []); self.unchanged()
                self.env.pop(key)

    def test_legacy_backend_without_explicit_provider_retains_mock_default_only(self):
        self.env['FIXTURE_RUNNING_BACKEND_PROVIDER'] = 'LEGACY'
        result = self.run_script('deploy-service.sh', 'frontend', 'c' * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.images.write_text(self.original); self.m1(); self.env['FIXTURE_RUNNING_BACKEND_PROVIDER'] = 'LEGACY'
        self.providers(**{DIGEST: 'M1'})
        result = self.run_script('deploy-service.sh', 'frontend', DIGEST)
        self.assertEqual(result.returncode, 65, result.stderr)

    def test_failed_mock_to_m1_restores_sha_and_reports_pending_backend_mode_recovery(self):
        self.m1(); self.providers(**{DIGEST: 'M1', OLD_SHA: 'MOCK'})
        self.env['DEPLOY_FAULT'] = 'deployment'
        result = self.run_script('deploy-service.sh', 'frontend', DIGEST)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Previous Frontend image restored', result.stderr)
        self.assertEqual(self.up_images(), [{'service': 'frontend', 'reference': DIGEST}, {'service': 'frontend', 'reference': OLD_SHA}])
        self.unchanged()

    def test_failed_m1_to_mock_restores_digest_and_reports_pending_backend_mode_recovery(self):
        original = self.original.replace(OLD_SHA, PREVIOUS_DIGEST); self.images.write_text(original)
        self.providers(**{PREVIOUS_DIGEST: 'M1', NEW_SHA: 'MOCK'}); self.env['DEPLOY_FAULT'] = 'deployment'
        result = self.run_script('deploy-service.sh', 'frontend', 'c' * 40)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Previous Frontend image restored', result.stderr)
        self.assertEqual(self.up_images()[-1], {'service': 'frontend', 'reference': PREVIOUS_DIGEST})
        self.unchanged(original)

    def test_same_mode_digest_rollback_uses_cache_even_if_registry_is_unavailable(self):
        self.m1(); original = self.original.replace(OLD_SHA, PREVIOUS_DIGEST); self.images.write_text(original)
        self.providers(**{PREVIOUS_DIGEST: 'M1', DIGEST: 'M1'})
        self.env.update(DEPLOY_FAULT='deployment', ROLLBACK_PULL_FAIL='1')
        result = self.run_script('deploy-service.sh', 'frontend', DIGEST)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Rollback succeeded', result.stderr)
        self.assertEqual(self.up_images()[-1]['reference'], PREVIOUS_DIGEST); self.unchanged(original)

    def test_failed_pull_never_recreates_even_the_previous_service(self):
        self.env['FIXTURE_PULL_FAIL'] = '1'
        result = self.run_script('deploy-service.sh', 'frontend', 'c' * 40)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.up_images(), []); self.unchanged()

    def test_bad_https_preflight_keeps_images_and_never_pulls(self):
        self.env['SMOKE_FAULT'] = 'tls'; self.m1(); self.providers(**{DIGEST: 'M1'})
        result = self.run_script('deploy-service.sh', 'frontend', DIGEST)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn(' pull ', self.calls()); self.assertEqual(self.up_images(), []); self.unchanged()


if __name__ == '__main__':
    unittest.main()
