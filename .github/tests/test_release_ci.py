"""Exercise release job conditions and the secret scanner's safe output boundary."""
import ast
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
FLOW = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
SPEC = importlib.util.spec_from_file_location('secret_scan', ROOT / '.github/scripts/check-secrets.py')
SCANNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCANNER)


def job(name):
    match = re.search(r'^  ' + name + r':\n(.*?)(?=^  [\w-]+:\n|\Z)', FLOW, re.M | re.S)
    return match.group(1) if match else None


def enabled(name, event, ref, cd='true', provider='M1'):
    body = job(name)
    if body is None:
        return False
    match = re.search(r'^    if: (.*)$', body, re.M)
    if not match:
        raise AssertionError('Publication/deployment must have an explicit job condition')
    expression = match.group(1)
    if expression == '>-':
        lines = []
        for line in body[match.end():].splitlines():
            if not line.strip():
                continue
            if line.startswith('      ') and not line.startswith('       '):
                lines.append(line.strip())
            else:
                break
        expression = ' '.join(lines)
    values = {'github.event_name': event, 'github.ref': ref, 'vars.CD_ENABLED': cd,
              'inputs.identity_provider': provider}
    expression = re.sub(r'github\.event_name|github\.ref|vars\.CD_ENABLED|inputs\.identity_provider',
                        lambda m: repr(values[m.group()]), expression)
    tree = ast.parse(expression.replace('&&', ' and ').replace('||', ' or '), mode='eval')
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq, ast.Constant)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise AssertionError('Unsupported condition; review evaluator before changing policy')
    return eval(compile(tree, '<job-condition>', 'eval'), {'__builtins__': {}}, {})


class ReleasePolicyTests(unittest.TestCase):
    def test_tag_trigger_and_existing_branch_triggers(self):
        push = FLOW.split('  push:\n', 1)[1].split('  workflow_dispatch:', 1)[0]
        self.assertRegex(push, r"tags:\s*\n\s*- 'v\*'")
        self.assertIn('- dev', push)
        self.assertIn('- master' if 'Backend CI' in FLOW else '- main', push)

    def test_tags_cannot_publish_or_deploy_even_with_cd_enabled_and_m1(self):
        for tag in ('v2.0-entrega4', 'v1.2.3', 'v-next'):
            for event in ('push', 'workflow_dispatch', 'release'):
                for name in ('publish', 'deploy'):
                    with self.subTest(tag=tag, event=event, job=name):
                        self.assertFalse(enabled(name, event, 'refs/tags/' + tag))

    def test_dev_cd_and_manual_publication_policy_is_preserved(self):
        if job('publish') is None:
            self.assertIsNone(job('deploy'))
            return
        self.assertTrue(enabled('publish', 'push', 'refs/heads/dev'))
        self.assertTrue(enabled('deploy', 'push', 'refs/heads/dev'))
        self.assertFalse(enabled('deploy', 'push', 'refs/heads/dev', cd='false'))
        self.assertFalse(enabled('publish', 'pull_request', 'refs/pull/1/merge'))
        self.assertFalse(enabled('deploy', 'workflow_dispatch', 'refs/heads/dev'))
        if 'Frontend CI' in FLOW:
            self.assertTrue(enabled('publish', 'workflow_dispatch', 'refs/heads/main'))
            self.assertFalse(enabled('publish', 'workflow_dispatch', 'refs/heads/main', provider='MOCK'))

    def test_existing_suites_and_gates_are_not_replaced(self):
        if 'Backend CI' in FLOW:
            self.assertIn('./mvnw -B clean verify', FLOW)
            self.assertIn('<minimum>0.85</minimum>', (ROOT / 'pom.xml').read_text())
        elif 'Frontend CI' in FLOW:
            for command in ('npm run lint', 'npm run test:coverage', 'npm run test:e2e', 'npm run build'):
                self.assertIn(command, FLOW)
            config = (ROOT / 'Front/frontend-atencion-ciudadana/vitest.config.js').read_text()
            for metric in ('statements', 'branches', 'functions', 'lines'):
                self.assertRegex(config, metric + r':\s*85')
            self.assertIn('needs: [configuration, validate, e2e]', job('publish'))
        else:
            self.assertIn('python3 -m unittest discover -s tests -v', FLOW)
            self.assertIn('terraform validate', FLOW)

    def test_scan_requires_full_checkout_and_only_uploads_sanitized_report(self):
        scan = job('secrets')
        self.assertIn('fetch-depth: 0', scan)
        self.assertIn('persist-credentials: false', scan)
        self.assertIn('check-secrets.py --output', scan)
        self.assertIn('path: ${{ runner.temp }}/secrets-summary.json', scan)
        self.assertIn('if: always()', scan)

    @unittest.skipUnless((ROOT / '.github/scripts/resolve-provider.sh').exists(), 'No provider selector in Infra')
    def test_actual_selector_uses_mock_for_tag_validation(self):
        bash = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('bash')
        with tempfile.TemporaryDirectory(prefix='m2-tag-policy-') as directory:
            output = Path(directory) / 'output'
            env = {**os.environ, 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/tags/v2.0-entrega4',
                   'CD_IDENTITY_PROVIDER': 'M1', 'INPUT_IDENTITY_PROVIDER': '', 'GITHUB_OUTPUT': str(output)}
            completed = subprocess.run([bash, '.github/scripts/resolve-provider.sh'], cwd=ROOT,
                                       env=env, capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(output.read_text().strip(), 'identity_provider=MOCK')


class SecretOutputTests(unittest.TestCase):
    def test_raw_content_is_never_exported(self):
        sensitive = 'PRIVATE_VALUE_FOR_REDACTION_CHECK'
        findings = [{'RuleID': 'test', 'File': 'sample.py', 'StartLine': 4, 'EndLine': 4,
                     'Commit': 'a' * 40, 'Secret': sensitive, 'Match': sensitive,
                     'Line': sensitive, 'Description': sensitive}]
        self.assertEqual(set(SCANNER.metadata(findings)[0]), set(SCANNER.FIELDS))
        self.assertNotIn(sensitive, str(SCANNER.metadata(findings)))

    def test_configuration_keeps_official_rules_and_limits_fixture_exception(self):
        config = tomllib.loads((ROOT / '.gitleaks.toml').read_text())
        self.assertTrue(config['extend']['useDefault'])
        self.assertNotIn('allowlists', config)
        for rule in config['rules']:
            for exception in rule.get('allowlists', []):
                self.assertEqual(exception['condition'], 'AND')
                self.assertEqual(exception['regexTarget'], 'secret')
                self.assertEqual(len(exception['regexes']), 1)
                self.assertNotIn('commits', exception)
                self.assertTrue(all(path.endswith('$') for path in exception['paths']))


if __name__ == '__main__':
    unittest.main()
