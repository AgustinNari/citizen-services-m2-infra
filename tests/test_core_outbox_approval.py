"""Offline fixtures only: never invoke SSH, Docker, a broker or a live database."""
import copy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import core_outbox_approval as approval
import run_core_outbox_approval as client

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-9222-222222222222'
C = '33333333-3333-4333-a333-333333333333'
D = '44444444-4444-4444-b444-444444444444'
SECRET = 'fictional-secret-must-not-appear'


def config(ids=''):
    return {'name': 'm2-fixture', 'services': {
        'backend': {'image': 'ghcr.io/juanmaguida/backend-damii:sha-' + 'a' * 40, 'environment': {
            approval.ENABLED: 'true', approval.AUTOMATIC: 'false', approval.APPROVED: ids,
            'CORE_OUTBOX_MAX_ATTEMPTS': '5', 'POSTGRES_PASSWORD': SECRET}},
        'postgres': {'image': 'postgres:17'}, 'frontend': {'image': 'fixture:frontend'},
        'caddy': {'environment': {'HTTPS_HOSTNAME': 'fixture.example.org', 'LEGACY_HTTP_IP': '192.0.2.1'}}}}


def row(status='PENDING', attempts=0, kind='ticketCreated'):
    return {'status': status, 'attempts': attempts, 'type': kind}


class FakeHost:
    def __init__(self, root):
        self.compose_dir = Path(root)
        self.runtime = Path(root) / '.env'
        self.images = Path(root) / 'images.env'
        self.runtime.write_bytes((f'# private fixture\r\nPOSTGRES_PASSWORD={SECRET}\r\n'
                                  'CORE_OUTBOX_ENABLED=true\r\nCORE_OUTBOX_AUTO_APPROVE_NEW_EVENTS=false\r\n'
                                  'CORE_OUTBOX_APPROVED_EVENT_IDS=\r\nOTHER="preserve $ and spaces"\r\n').encode())
        self.images.write_bytes(b'BACKEND_IMAGE=fixture-immutable\nFRONTEND_IMAGE=unchanged\n')
        self.running_ids = []
        self.database = {A: row(), B: row(), C: row(), D: row()}
        self.applies = 0
        self.applied = False
        self.health_checks = 0
        self.health = True
        self.fail_apply = False
        self.post_status = 'PUBLISHED'
        self.fail_query_after_apply = False
        self.queries = 0
        self.drift = False
        self.mutate_candidate = False
        self.clock = 0

    def config(self, runtime=None):
        value = (runtime or self.runtime).read_text()
        ids = next(line.split('=', 1)[1] for line in value.splitlines() if line.startswith(approval.APPROVED + '='))
        result = config(ids)
        if runtime and self.mutate_candidate:
            result['services']['frontend']['image'] = 'unexpected'
        return result

    def running(self, selected):
        approval.require(not self.drift, 'BACKEND_DRIFT')
        return self.running_ids

    def rows(self, ids):
        self.queries += 1
        if self.fail_query_after_apply and self.applies:
            raise approval.Rejected('DATABASE_UNAVAILABLE')
        if self.applied:
            for event in self.database.values():
                if event['status'] == 'PENDING' and self.post_status != 'PENDING':
                    event['status'] = self.post_status
        return {value: self.database[value].copy() for value in ids if value in self.database}

    def healthy(self, selected):
        self.health_checks += 1
        return self.health if not callable(self.health) else self.health(self.health_checks)

    def apply(self):
        self.applies += 1
        approval.require(not self.fail_apply)
        self.applied = True
        self.running_ids = approval.normalize_ids(self.config()['services']['backend']['environment'][approval.APPROVED],
                                                  approval.MAX_APPROVED, allow_empty=True)

    def tick(self, seconds):
        self.clock += seconds


class InputTests(unittest.TestCase):
    def test_canonicalizes_deduplicates_and_supports_newlines(self):
        self.assertEqual(approval.normalize_ids(f' {A.upper()} , {B}\r\n\n{A}\n{C} '), [A, B, C])

    def test_rejects_invalid_uuid_version_variant_shell_and_empty_fields(self):
        for value in ('', ' ', A[:-1], A + '0', A.replace('-4111-', '-1111-'),
                      A.replace('-8111-', '-7111-'), '{' + A + '}', A + ',, ' + B,
                      A + ',', ',' + A, A + ';id', '$(touch /tmp/fail)', A + '\x00', A + ' ' + B):
            with self.subTest(value=value), self.assertRaises(approval.Rejected):
                approval.normalize_ids(value)

    def test_limits_unique_ids_and_input_bytes(self):
        ids = [f'{index:08x}-1111-4111-8111-111111111111' for index in range(51)]
        self.assertEqual(len(approval.normalize_ids(','.join(ids[:50]))), 50)
        for value in (','.join(ids), ' ' * (approval.MAX_INPUT + 1)):
            with self.assertRaises(approval.Rejected):
                approval.normalize_ids(value)

    def test_wire_protocol_rejects_extra_fields_and_embedded_delimiters(self):
        self.assertEqual(approval.request_ids(json.dumps({'event_ids': [A.upper(), A]})), [A])
        for data in ({'event_ids': [A], 'command': 'sh'}, {'event_ids': A}, {'event_ids': [A + ',' + B]},
                     {'event_ids': []}, {'event_ids': [1]}, [], None):
            with self.subTest(data=data), self.assertRaises(approval.Rejected):
                approval.request_ids(json.dumps(data))

    def test_dotenv_preserves_every_unrelated_byte_and_comments(self):
        original = b'# keep\r\nSECRET="literal $ $(id)"\r\nCORE_OUTBOX_APPROVED_EVENT_IDS=\r\nLAST=no-newline'
        self.assertEqual(approval.env_candidate(original, [A]),
                         original.replace(b'CORE_OUTBOX_APPROVED_EVENT_IDS=\r\n',
                                          ('CORE_OUTBOX_APPROVED_EVENT_IDS=' + A + '\r\n').encode()))
        self.assertEqual(approval.env_candidate(b'OTHER=x', [A]),
                         ('OTHER=x\nCORE_OUTBOX_APPROVED_EVENT_IDS=' + A + '\n').encode())

    def test_dotenv_ambiguous_duplicate_or_multiline_assignments_rejected(self):
        for value in (b'CORE_OUTBOX_APPROVED_EVENT_IDS=\nexport CORE_OUTBOX_APPROVED_EVENT_IDS=\n',
                      b'CORE_OUTBOX_APPROVED_EVENT_IDS="\nsecret"\n',
                      b'CORE_OUTBOX_APPROVED_EVENT_IDS=${OTHER}\n'):
            with self.assertRaises(approval.Rejected):
                approval.env_candidate(value, [A])


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.host = FakeHost(self.directory.name)
        self.original = self.host.runtime.read_bytes()
        self.images = self.host.images.read_bytes()
        # Linux permissions/fsync are tested separately; this suite also runs on Windows.
        self.private = patch.object(approval, 'private_file').start()
        self.addCleanup(patch.stopall)
        patch.object(approval, 'atomic_write', side_effect=lambda path, content: path.write_bytes(content)).start()

    def execute(self, ids=None):
        result = approval.approve(self.host, ids or [A], sleep=self.host.tick, monotonic=lambda: self.host.clock)
        self.assertEqual(self.host.images.read_bytes(), self.images)
        self.assertEqual(list(self.host.compose_dir.glob('.env.outbox.*')), [])
        self.assertNotIn(SECRET, json.dumps(result))
        return result

    def approve_on_disk(self, ids):
        self.host.runtime.write_bytes(approval.env_candidate(self.original, ids))

    def test_new_batch_confirmed_after_one_backend_recreation(self):
        result = self.execute([A, B])
        self.assertEqual(result['code'], 'PUBLISHED')
        self.assertEqual(self.host.applies, 1)
        self.assertTrue(result['env_written'] and result['recreated'])
        self.assertEqual(self.host.runtime.read_bytes(), approval.env_candidate(self.original, [A, B]))
        self.assertGreaterEqual(self.host.health_checks, 3)

    def test_preserves_pending_approvals_from_disk_and_running_after_partial_attempt(self):
        self.approve_on_disk([B, D])
        self.host.running_ids = [C]
        self.host.database[D] = row('PUBLISHED')
        self.execute()
        self.assertEqual(self.host.runtime.read_bytes(), approval.env_candidate(self.original, [A, B, C]))

    def test_repeated_already_published_events_never_recreate(self):
        self.approve_on_disk([A])
        self.host.running_ids = [A]
        self.host.database[A] = row('PUBLISHED')
        result = self.execute()
        self.assertEqual(result['code'], 'PUBLISHED')
        self.assertTrue(result['env_written'])  # Prunes terminal approval on disk only.
        self.assertEqual(self.host.applies, 0)

    def test_pending_already_running_keeps_processing_without_recreation(self):
        self.approve_on_disk([A])
        self.host.running_ids = [A]
        result = self.execute()
        self.assertEqual(result['code'], 'PENDING')
        self.assertFalse(result['env_written'] or result['recreated'])
        self.assertEqual(self.host.applies, 0)

    def test_mixed_invalid_batch_never_changes_env_or_recreates(self):
        for event in (None, row('FAILED'), row(attempts=5), row(kind='unknownType')):
            with self.subTest(event=event):
                if event is None:
                    self.host.database.pop(B, None)
                else:
                    self.host.database[B] = event
                result = self.execute([A, B])
                self.assertEqual(result['code'], 'BATCH_REJECTED')
                self.assertEqual(self.host.runtime.read_bytes(), self.original)
                self.assertEqual(self.host.applies, 0)

    def test_preflight_health_failure_is_non_mutating(self):
        self.host.health = False
        self.assertEqual(self.execute()['code'], 'HEALTH_PREFLIGHT_FAILED')
        self.assertEqual(self.host.runtime.read_bytes(), self.original)
        self.assertEqual(self.host.applies, 0)

    def test_unrelated_configuration_drift_rejected(self):
        self.host.drift = True
        self.assertEqual(self.execute()['code'], 'BACKEND_DRIFT')
        self.assertEqual(self.host.runtime.read_bytes(), self.original)

    def test_candidate_cannot_change_frontend_or_other_settings(self):
        self.host.mutate_candidate = True
        self.assertEqual(self.execute()['code'], 'CONFIGURATION_REJECTED')
        self.assertEqual(self.host.runtime.read_bytes(), self.original)
        self.assertEqual(self.host.applies, 0)

    def test_apply_failure_retains_approvals_and_repeat_recovers(self):
        self.host.fail_apply = True
        result = self.execute()
        self.assertEqual(result['code'], 'APPLY_FAILED')
        self.assertTrue(result['env_written'])
        self.assertEqual(result['events'][0]['status'], 'PENDING')
        self.host.fail_apply = False
        self.assertEqual(self.execute()['code'], 'PUBLISHED')
        self.assertEqual(self.host.applies, 2)

    def test_health_failure_does_not_claim_success_even_if_event_was_published(self):
        self.host.health = lambda count: count == 1
        result = self.execute()
        self.assertEqual(result['code'], 'HEALTH_FAILED')
        self.assertEqual(result['events'][0]['status'], 'PUBLISHED')
        self.assertEqual(result['health'], 'UNVERIFIED')

    def test_postcheck_health_failure_without_recreation(self):
        self.host.database[A] = row('PUBLISHED')
        self.host.health = lambda count: count == 1
        self.assertEqual(self.execute()['code'], 'HEALTH_FAILED')
        self.assertEqual(self.host.applies, 0)

    def test_broker_pending_and_failure_never_report_publication_success(self):
        for status in ('PENDING', 'FAILED'):
            with self.subTest(status=status):
                self.host.post_status = status
                self.assertEqual(self.execute()['code'], status)
                self.host.running_ids = []
                self.host.applied = False
                self.host.database[A] = row()
                self.host.runtime.write_bytes(self.original)

    def test_database_loss_after_apply_marks_result_unknown(self):
        self.host.fail_query_after_apply = True
        result = self.execute()
        self.assertEqual(result['code'], 'DATABASE_UNAVAILABLE')
        self.assertEqual(result['events'][0]['status'], 'UNKNOWN')

    def test_manual_edit_detected_before_atomic_replace(self):
        original_config = self.host.config
        def mutate(runtime=None):
            result = original_config(runtime)
            if runtime:
                self.host.runtime.write_bytes(self.original + b'OPERATOR_CHANGE=x\n')
            return result
        self.host.config = mutate
        self.assertEqual(self.execute()['code'], 'CONFIGURATION_REJECTED')
        self.assertTrue(self.host.runtime.read_bytes().endswith(b'OPERATOR_CHANGE=x\n'))
        self.assertEqual(self.host.applies, 0)

    def test_preserved_allowlist_cannot_exceed_backend_limit(self):
        existing = [f'{index:08x}-1111-4111-8111-111111111111' for index in range(1000)]
        self.approve_on_disk(existing)
        for event_id in existing:
            self.host.database[event_id] = row()
        before = self.host.runtime.read_bytes()
        self.assertEqual(self.execute()['code'], 'CONFIGURATION_REJECTED')
        self.assertEqual(self.host.runtime.read_bytes(), before)
        self.assertEqual(self.host.applies, 0)

    def test_partial_batch_reports_each_observed_terminal_result(self):
        previous = self.host.rows
        def partial(ids):
            self.host.post_status = 'PENDING'
            if self.host.applies:
                self.host.database[A] = row('PUBLISHED')
                self.host.database[B] = row('FAILED')
            return previous(ids)
        self.host.rows = partial
        report = self.execute([A, B])
        self.assertEqual(report['code'], 'FAILED')
        self.assertEqual(report['events'], [{'id': A, 'status': 'PUBLISHED'}, {'id': B, 'status': 'FAILED'}])


class CommandBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.host = approval.Host(ROOT)

    def test_sql_is_bounded_read_only_and_never_selects_payload_or_credentials(self):
        with patch.object(self.host, 'compose', return_value=f'{A}|PENDING|0|f|ticketCreated\n') as command:
            self.assertEqual(self.host.rows([A])[A]['status'], 'PENDING')
        args, kwargs = command.call_args
        self.assertEqual(args[:4], ('exec', '-T', 'postgres', 'sh'))
        self.assertIn('BEGIN READ ONLY;', kwargs['data'])
        self.assertIn("statement_timeout='5s'", kwargs['data'])
        for forbidden in ('UPDATE ', 'INSERT ', 'DELETE ', 'payload', 'ticket_id', 'password'):
            self.assertNotIn(forbidden, kwargs['data'])
        with patch.object(self.host, 'compose') as command, self.assertRaises(approval.Rejected):
            self.host.rows([A + "'); DELETE FROM outbox_events; --"])
        command.assert_not_called()

    def test_bad_database_output_is_not_accepted_as_confirmation(self):
        for output in (f'{A}|PUBLISHED|1|f|ticketCreated', f'{A}|SURPRISE|0|t|ticketCreated',
                       SECRET, f'{A}|PENDING|0|f|ticketCreated\n{A}|PENDING|0|f|ticketCreated'):
            with patch.object(self.host, 'compose', return_value=output), self.assertRaises(approval.Rejected) as failure:
                self.host.rows([A])
            self.assertEqual(str(failure.exception), 'DATABASE_UNAVAILABLE')

    def test_large_reconciliation_is_bounded_independently_of_public_input_bytes(self):
        ids = [f'{index:08x}-1111-4111-8111-111111111111' for index in range(2050)]
        self.assertGreater(len(','.join(ids)), approval.MAX_INPUT)
        with patch.object(self.host, 'compose', return_value='') as command:
            self.assertEqual(self.host.rows(ids), {})
        self.assertEqual(command.call_args.kwargs['data'].count('::uuid'), len(ids))
        with patch.object(self.host, 'compose') as command, self.assertRaises(approval.Rejected):
            self.host.rows(ids + [A])
        command.assert_not_called()

    def test_actual_compose_argv_preserves_project_files_and_only_applies_backend(self):
        with patch.object(approval, 'run_private', return_value='') as command:
            self.host.apply()
        args = command.call_args.args[0]
        self.assertEqual(args[-7:], ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'backend'])
        self.assertIn(str(ROOT / 'compose/.env'), args)
        self.assertIn(str(ROOT / 'compose/images.env'), args)
        self.assertIn('--project-directory', args)
        self.assertNotIn('down', args)
        self.assertNotIn('pull', args)

    def test_config_refuses_automatic_or_disabled_publishing(self):
        for key, value in ((approval.AUTOMATIC, 'true'), (approval.ENABLED, 'false')):
            selected = config()
            selected['services']['backend']['environment'][key] = value
            with patch.object(self.host, 'compose', return_value=json.dumps(selected)), \
                    patch.object(approval, 'run_private'), self.assertRaises(approval.Rejected):
                self.host.config()

    def test_running_image_project_and_unrelated_env_must_match(self):
        selected = config()
        info = {'State': {'Running': True}, 'Image': 'sha256:' + 'a' * 64,
                'Config': {'Image': selected['services']['backend']['image'],
                'Labels': {'com.docker.compose.project': selected['name'], 'com.docker.compose.service': 'backend',
                           'com.docker.compose.config-hash': 'f' * 64},
                'Env': [key + '=' + value for key, value in selected['services']['backend']['environment'].items()]}}
        def compose(*args, **kwargs):
            return 'd' * 64 if args[0] == 'ps' else 'backend ' + 'f' * 64
        def private(args, **kwargs):
            return json.dumps([info]) if args[1] == 'inspect' else 'sha256:' + 'a' * 64
        with patch.object(self.host, 'compose', side_effect=compose), \
                patch.object(approval, 'run_private', side_effect=private):
            self.assertEqual(self.host.running(selected), [])
        for change in ('image', 'project', 'secret', 'auto', 'service-config', 'cached-image'):
            modified = copy.deepcopy(info)
            if change == 'image': modified['Config']['Image'] = 'unrelated-image'
            if change == 'project': modified['Config']['Labels']['com.docker.compose.project'] = 'other'
            if change == 'secret': modified['Config']['Env'].append('POSTGRES_PASSWORD=changed')
            if change == 'auto': modified['Config']['Env'].append(approval.AUTOMATIC + '=true')
            if change == 'service-config': modified['Config']['Labels']['com.docker.compose.config-hash'] = '0' * 64
            if change == 'cached-image': modified['Image'] = 'sha256:' + '0' * 64
            def modified_private(args, **kwargs):
                return json.dumps([modified]) if args[1] == 'inspect' else 'sha256:' + 'a' * 64
            with patch.object(self.host, 'compose', side_effect=compose), \
                    patch.object(approval, 'run_private', side_effect=modified_private), self.assertRaises(approval.Rejected):
                self.host.running(selected)

    def test_command_errors_never_include_raw_output(self):
        result = subprocess.CompletedProcess(['fixture'], 1, SECRET, SECRET)
        with patch.object(approval.subprocess, 'run', return_value=result), self.assertRaises(approval.Rejected) as failure:
            approval.run_private(['fixture'])
        self.assertNotIn(SECRET, str(failure.exception))

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership/fsync validated on Linux CI')
    def test_atomic_private_env_replacement_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_bytes(b'private fixture\n')
            path.chmod(0o600)
            approval.private_file(path)
            approval.atomic_write(path, b'updated fixture\n')
            self.assertEqual(path.read_bytes(), b'updated fixture\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            path.chmod(0o644)
            with self.assertRaises(approval.Rejected): approval.private_file(path)
            link = Path(directory) / 'link'
            link.symlink_to(path)
            with self.assertRaises(approval.Rejected): approval.private_file(link)


class ClientTests(unittest.TestCase):
    def report(self, code='PUBLISHED'):
        return {'version': 1, 'code': code, 'env_written': True, 'recreated': True, 'health': 'UP',
                'events': [{'id': A, 'status': code}]}

    def settings(self):
        return {'OUTBOX_APPROVALS_ENABLED': 'true', 'LIGHTSAIL_HOST': '192.0.2.1', 'LIGHTSAIL_USER': 'ubuntu',
                'OUTBOX_SSH_PRIVATE_KEY': SECRET, 'LIGHTSAIL_KNOWN_HOSTS': '192.0.2.1 ssh-ed25519 fictional-fixture'}

    def test_ssh_strict_pinning_fixed_command_private_files_and_json_stdin(self):
        def remote(args, **kwargs):
            self.assertEqual(args[-1], 'approve-core-outbox-v1')
            self.assertIn('StrictHostKeyChecking=yes', args)
            self.assertIn('IdentitiesOnly=yes', args)
            self.assertEqual(json.loads(kwargs['input']), {'event_ids': [A]})
            self.assertNotIn('OUTBOX_SSH_PRIVATE_KEY', kwargs['env'])
            key_path = Path(args[args.index('-i') + 1])
            self.assertEqual(key_path.read_text().strip(), SECRET)
            self.temporary = key_path.parent
            return subprocess.CompletedProcess(args, 0, json.dumps(self.report()), SECRET)
        with patch.object(client.subprocess, 'run', side_effect=remote):
            self.assertEqual(client.connect([A], self.settings())['code'], 'PUBLISHED')
        self.assertFalse(self.temporary.exists())

    def test_wrong_host_user_or_disabled_never_connect(self):
        for key, value in (('LIGHTSAIL_HOST', '-oProxyCommand=id'), ('LIGHTSAIL_HOST', 'a;id'),
                           ('LIGHTSAIL_USER', 'ubuntu;id'), ('OUTBOX_APPROVALS_ENABLED', 'false'),
                           ('OUTBOX_SSH_PRIVATE_KEY', ''), ('LIGHTSAIL_KNOWN_HOSTS', '')):
            settings = self.settings(); settings[key] = value
            with patch.object(client.subprocess, 'run') as remote, self.assertRaises(approval.Rejected):
                client.connect([A], settings)
            remote.assert_not_called()

    def test_report_rejects_unknown_fields_statuses_ids_and_false_success(self):
        for change in ('extra', 'status', 'id', 'pending', 'health', 'code', 'version'):
            report = self.report()
            if change == 'extra': report['secret'] = SECRET
            if change == 'status': report['events'][0]['status'] = SECRET
            if change == 'id': report['events'][0]['id'] = B
            if change == 'pending': report['events'][0]['status'] = 'PENDING'
            if change == 'health': report['health'] = 'UNVERIFIED'
            if change == 'code': report['code'] = SECRET
            if change == 'version': report['version'] = True
            with self.assertRaises(approval.Rejected): client.validate_report(json.dumps(report), [A])

    def test_partial_pending_is_visible_but_not_successful(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary'
            with patch.dict(os.environ, {'OUTBOX_EVENT_IDS': A, 'GITHUB_STEP_SUMMARY': str(path)}), \
                    patch.object(client, 'connect', return_value=self.report('PENDING')), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(client.main(), 1)
            self.assertIn('PENDING', path.read_text(encoding='utf-8'))
            self.assertNotIn(SECRET, output.getvalue())

    def test_transport_or_remote_failure_suppresses_secrets_and_reports_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary'
            with patch.dict(os.environ, {'OUTBOX_EVENT_IDS': A, 'GITHUB_STEP_SUMMARY': str(path)}), \
                    patch.object(client, 'connect', side_effect=RuntimeError(SECRET)), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(client.main(), 1)
            self.assertNotIn(SECRET, output.getvalue() + path.read_text(encoding='utf-8'))
            self.assertIn('UNKNOWN', path.read_text(encoding='utf-8'))

    def test_injection_rejected_before_connect(self):
        with patch.dict(os.environ, {'OUTBOX_EVENT_IDS': '$(id)'}), patch.object(client, 'connect') as remote, \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(client.main(), 1)
        remote.assert_not_called()


class WorkflowSecurityTests(unittest.TestCase):
    def test_live_approval_requires_main_and_fixed_environment(self):
        text = (ROOT / '.github/workflows/approve-core-outbox.yml').read_text()
        self.assertRegex(text, r"(?m)^    if: github\.event_name == 'workflow_dispatch' && github\.ref == 'refs/heads/main'$")
        self.assertRegex(text, r'(?m)^    environment: core-outbox-approval$')
        self.assertNotIn('refs/heads/dev', text)
        self.assertNotIn('pull_request_target:', text)
        self.assertNotIn('pull_request:', text)
        self.assertIn('cancel-in-progress: false', text)

    def test_user_input_is_only_step_data_and_checkout_is_immutable(self):
        text = (ROOT / '.github/workflows/approve-core-outbox.yml').read_text()
        # Exactly one interpolation of user-controlled input, into the environment.
        self.assertEqual(text.count('${{ inputs.event_ids }}'), 1)
        self.assertIn('OUTBOX_EVENT_IDS: ${{ inputs.event_ids }}', text)
        self.assertRegex(text, r'uses: actions/checkout@[0-9a-f]{40}\b')
        self.assertIn('persist-credentials: false', text)
        self.assertNotIn('contents: write', text)
        self.assertNotIn('id-token: write', text)

    def test_required_offline_check_never_needs_secrets_or_skips_paths(self):
        text = (ROOT / '.github/workflows/core-outbox-approval-ci.yml').read_text()
        self.assertIn('name: Core Outbox approval offline tests', text)
        self.assertIn('branches: [main, dev]', text)
        self.assertNotRegex(text, r'(?m)^\s*(?:paths|paths-ignore|if|environment):')
        self.assertNotIn('secrets.', text)
        self.assertNotIn('pull_request_target:', text)


if __name__ == '__main__':
    unittest.main()
