"""Query security/format tests and optional isolated PostgreSQL 17 contract tests."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import core_outbox_query as query
import run_core_outbox_query as client
import run_core_outbox_approval as approval_client
from test_core_outbox_approval import A, B, C, SECRET
import test_core_outbox_shell as shell_fixtures

TICKET = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER_TICKET = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
SELECTION = {'lookup_by': 'ticket_id', 'lookup_id': TICKET}


def row(event_id=A, **overrides):
    value = dict(event_id=event_id, ticket_id=TICKET, correlation_id=C,
                 created_at='2026-11-15T01:00:00+00:00', published_at='2026-11-15T02:30:00+00:00',
                 published_at_argentina='2026-11-14T23:30:00', status='PUBLISHED', area='M3',
                 event_type='ticketUpdated', update_type='ROUTED')
    value.update(overrides)
    return value


class QueryContractTests(unittest.TestCase):
    def test_both_modes_normalize_exactly_one_uuid_and_follow_backend_contract(self):
        self.assertEqual(query.lookup('event_id', ' ' + A.upper() + ' '), {'lookup_by': 'event_id', 'lookup_id': A})
        self.assertEqual(query.lookup('ticket_id', TICKET.upper()), SELECTION)
        # ticket/correlation contracts allow a canonical UUID independently of eventId's v4 requirement.
        generic = 'aaaaaaaa-aaaa-1aaa-8aaa-aaaaaaaaaaaa'
        self.assertEqual(query.lookup('ticket_id', generic)['lookup_id'], generic)
        with self.assertRaises(query.Rejected): query.lookup('event_id', generic)

    def test_injections_batches_bad_modes_and_extra_json_keys_are_rejected(self):
        for mode, value in [('ticket_id; DELETE', TICKET), ('payload', A), ('ticket_id', A + ',' + B),
                            ('event_id', A + "' OR TRUE --"), ('event_id', '$(id)'), ('ticket_id', '{' + TICKET + '}'),
                            ('event_id', A + '\n' + B), ('event_id', ''), ('ticket_id', 'a' * 4097)]:
            with self.subTest(mode=mode, value=value), self.assertRaises(query.Rejected):
                query.lookup(mode, value)
        for value in ({**SELECTION, 'sql': 'DELETE'}, {'event_ids': [A]}, {'lookup_by': 'event_id'}, []):
            with self.assertRaises((query.Rejected, ValueError)): query.request(json.dumps(value))
        with self.assertRaises(query.Rejected): query.request(b' ' * (query.MAX_REQUEST + 1))

    def test_sql_has_only_fixed_readonly_projection_and_bounded_deterministic_order(self):
        sql = query.sql_for(SELECTION)
        self.assertTrue(sql.startswith('BEGIN READ ONLY;'))
        self.assertIn("statement_timeout='5s'", sql)
        self.assertIn("lock_timeout='2s'", sql)
        self.assertIn('LIMIT 101', sql)
        self.assertIn('ORDER BY created_at DESC NULLS LAST, event_id DESC', sql)
        self.assertIn("AT TIME ZONE 'America/Argentina/Buenos_Aires'", sql)
        self.assertIn("WHERE ticket_id = '" + TICKET + "'::uuid", sql)
        for forbidden in ('SELECT *', 'UPDATE ', 'INSERT ', 'DELETE ', 'FOR UPDATE', 'COPY ', 'CREATE ',
                          'citizen', 'tracking', 'password', 'retry_count', 'payload,'):
            self.assertNotIn(forbidden, sql)
        self.assertIn('default_transaction_read_only=on', query.PSQL)
        self.assertIn('psql -X -q -A -t -v ON_ERROR_STOP=1', query.PSQL)

    def test_dates_nulls_optional_fields_and_midnight_conversion(self):
        published = row()
        pending = row(B, created_at=None, published_at=None, published_at_argentina=None,
                      correlation_id=None, area=None, status='PENDING', event_type='ticketCreated', update_type=None)
        self.assertEqual(query.validate_rows([published, pending], SELECTION), [published, pending])
        rendered = client.summary({'code': 'OK', 'rows': [published, pending], 'truncated': False}, SELECTION)
        self.assertIn('2026-11-14T23:30:00', rendered)
        self.assertIn('NULL', rendered)
        self.assertIn('```csv\n' + ','.join(query.FIELDS), rendered)
        self.assertIn('ROUTED', rendered)

    def test_private_unexpected_or_malformed_metadata_cannot_reach_summary(self):
        for changes in ({'payload': {'token': SECRET}}, {'area': SECRET}, {'correlation_id': SECRET},
                        {'event_type': SECRET}, {'update_type': SECRET}, {'status': SECRET},
                        {'created_at': SECRET}, {'published_at_argentina': '2026-11-15T02:30:00'},
                        {'published_at_argentina': None}, {'published_at': '2026-11-15T02:30:00-03:00'},
                        {'ticket_id': OTHER_TICKET}):
            with self.subTest(changes=changes), self.assertRaises(query.Rejected):
                query.validate_rows([row(**changes)], SELECTION)
        with self.assertRaises(query.Rejected): query.validate_rows([row(), row()], SELECTION)
        with self.assertRaises(query.Rejected): query.validate_rows([row(A), row(B)], SELECTION)
        with self.assertRaises(query.Rejected): query.validate_rows([row()], {'lookup_by': 'event_id', 'lookup_id': B})

    def test_no_results_is_successful_and_truncation_is_explicit(self):
        empty = {'version': 1, 'code': 'NO_RESULTS', 'rows': [], 'truncated': False}
        self.assertEqual(query.validate_report(json.dumps(empty), SELECTION), empty)
        self.assertIn('Sin eventos', client.summary(empty, SELECTION))
        rows = [row(f'{index:08x}-1111-4111-8111-111111111111') for index in reversed(range(100))]
        report = {'version': 1, 'code': 'OK', 'rows': rows, 'truncated': True}
        self.assertEqual(query.validate_report(json.dumps(report), SELECTION), report)
        self.assertIn('Resultado parcial', client.summary(report, SELECTION))
        report['rows'] = rows[:1]
        with self.assertRaises(query.Rejected): query.validate_report(json.dumps(report), SELECTION)

    def test_response_cannot_smuggle_fields_bad_codes_or_excess_rows(self):
        for report in ({'version': True, 'code': 'OK', 'rows': [row()], 'truncated': False},
                       {'version': 1, 'code': SECRET, 'rows': [], 'truncated': False},
                       {'version': 1, 'code': 'NO_RESULTS', 'rows': [row()], 'truncated': False},
                       {'version': 1, 'code': 'OK', 'rows': [row()], 'truncated': False, 'secret': SECRET}):
            with self.assertRaises(query.Rejected): query.validate_report(json.dumps(report), SELECTION)
        with self.assertRaises(query.Rejected): query.validate_rows([row()] * 101, SELECTION)


class QueryHostTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.host = query.Host(self.directory.name)
        self.host.compose_dir.mkdir()
        self.host.runtime.write_bytes(b'# fictional private fixture\nUNCHANGED=fixture\n')
        self.original = self.host.runtime.read_bytes()
        patcher = patch.object(query, 'private_file')
        patcher.start(); self.addCleanup(patcher.stop)

    def execute(self, rows):
        with patch.object(self.host, 'compose', return_value=json.dumps(rows)) as command:
            result = query.query(self.host, SELECTION)
        self.assertEqual(self.host.runtime.read_bytes(), self.original)
        self.assertEqual(list(self.host.compose_dir.iterdir()), [self.host.runtime])
        self.assertEqual(command.call_count, 1)
        args, kwargs = command.call_args
        self.assertEqual(args, ('exec', '-T', 'postgres', 'sh', '-c', query.PSQL))
        self.assertEqual(kwargs['timeout'], 15)
        self.assertEqual(kwargs['data'], query.sql_for(SELECTION))
        return result

    def test_query_uses_only_one_readonly_psql_call_and_never_edits_env(self):
        self.assertEqual(self.execute([row()])['code'], 'OK')
        self.assertEqual(self.execute([])['code'], 'NO_RESULTS')

    def test_reads_101_but_exposes_only_100_and_marks_truncation(self):
        rows = [row(f'{index:08x}-1111-4111-8111-111111111111') for index in reversed(range(101))]
        report = self.execute(rows)
        self.assertTrue(report['truncated'])
        self.assertEqual(report['rows'], rows[:100])

    def test_invalid_input_never_reaches_compose(self):
        with patch.object(self.host, 'compose') as command, self.assertRaises(query.Rejected):
            query.query(self.host, {'lookup_by': 'event_id', 'lookup_id': '$(id)'})
        command.assert_not_called()

    def test_db_errors_and_private_values_are_not_propagated(self):
        with patch.object(self.host, 'compose', side_effect=RuntimeError(SECRET)), self.assertRaises(query.Rejected) as failure:
            query.query(self.host, SELECTION)
        self.assertEqual(str(failure.exception), 'QUERY_FAILED')
        with patch.object(self.host, 'compose', return_value=json.dumps([row(area=SECRET)])), self.assertRaises(query.Rejected) as failure:
            query.query(self.host, SELECTION)
        self.assertEqual(str(failure.exception), 'INVALID_RESULT')


class QueryClientTests(unittest.TestCase):
    def test_query_reuses_restricted_transport_without_broadening_commands(self):
        with patch.object(approval_client.subprocess, 'run') as remote, self.assertRaises(query.Rejected):
            approval_client.ssh_request(SELECTION, {}, command='query-core-outbox-v1; id')
        remote.assert_not_called()

    def test_query_transport_pins_host_key_sends_json_stdin_and_hides_private_key(self):
        settings = {'OUTBOX_APPROVALS_ENABLED': 'true', 'LIGHTSAIL_HOST': '192.0.2.1', 'LIGHTSAIL_USER': 'ubuntu',
                    'OUTBOX_SSH_PRIVATE_KEY': SECRET, 'LIGHTSAIL_KNOWN_HOSTS': '192.0.2.1 ssh-ed25519 fixture'}
        def ssh(args, **kwargs):
            self.assertEqual(args[-1], 'query-core-outbox-v1')
            self.assertIn('StrictHostKeyChecking=yes', args)
            self.assertIn('IdentitiesOnly=yes', args)
            self.assertEqual(json.loads(kwargs['input']), SELECTION)
            self.assertNotIn('OUTBOX_SSH_PRIVATE_KEY', kwargs['env'])
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(approval_client.subprocess, 'run', side_effect=ssh):
            approval_client.ssh_request(SELECTION, settings, command='query-core-outbox-v1', timeout=730)

    def test_runner_writes_metadata_only_to_summary_and_suppresses_remote_errors(self):
        for raw, returncode in ((json.dumps({'version': 1, 'code': 'OK', 'rows': [row()], 'truncated': False}), 0),
                                (SECRET, 1), (json.dumps({'version': 1, 'code': 'OK', 'rows': [row(area=SECRET)], 'truncated': False}), 0)):
            with tempfile.TemporaryDirectory() as directory:
                summary_path = Path(directory) / 'summary'
                with patch.dict(os.environ, {'OUTBOX_LOOKUP_BY': 'ticket_id', 'OUTBOX_LOOKUP_ID': TICKET,
                                             'GITHUB_STEP_SUMMARY': str(summary_path)}), \
                        patch.object(client, 'ssh_request', return_value=subprocess.CompletedProcess([], returncode, raw, SECRET)), \
                        patch('sys.stdout', new_callable=io.StringIO) as output:
                    code = client.main()
                combined = output.getvalue() + summary_path.read_text(encoding='utf-8')
                self.assertNotIn(SECRET, combined)
                self.assertEqual(code == 0, raw.startswith('{') and SECRET not in raw)
                self.assertNotIn(A, output.getvalue())

    def test_bad_user_input_never_connects_and_cannot_inject_log_commands(self):
        with patch.dict(os.environ, {'OUTBOX_LOOKUP_BY': 'event_id', 'OUTBOX_LOOKUP_ID': '::error::' + SECRET}), \
                patch.object(client, 'ssh_request') as remote, patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(client.main(), 1)
        remote.assert_not_called()
        self.assertNotIn(SECRET, output.getvalue())

    def test_workflow_reuses_main_only_environment_and_keeps_inputs_out_of_shell(self):
        text = (ROOT / '.github/workflows/query-core-outbox.yml').read_text()
        self.assertIn('environment: core-outbox-approval', text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn('permissions:\n  contents: read', text)
        self.assertEqual(text.count('${{ inputs.lookup_by }}'), 1)
        self.assertEqual(text.count('${{ inputs.lookup_id }}'), 1)
        self.assertIn('OUTBOX_LOOKUP_BY: ${{ inputs.lookup_by }}', text)
        self.assertIn('OUTBOX_LOOKUP_ID: ${{ inputs.lookup_id }}', text)
        self.assertNotIn('pull_request_target:', text)


@unittest.skipUnless(shell_fixtures.BASH, 'Bash required')
class QueryWrapperTests(unittest.TestCase):
    setUp = shell_fixtures.WrapperTests.setUp
    command = shell_fixtures.WrapperTests.command
    execute = shell_fixtures.WrapperTests.execute

    def test_query_is_the_only_additional_fixed_command_and_has_shorter_runtime(self):
        self.env['SSH_ORIGINAL_COMMAND'] = 'query-core-outbox-v1'
        self.assertEqual(self.execute().returncode, 0)
        command = (self.root / 'timeout-call').read_text()
        self.assertIn('--signal=TERM --kill-after=10 60 python3', command)
        self.assertIn('core_outbox_query.py', command)
        self.assertNotIn('core_outbox_approval.py', command)

    def test_query_arguments_shells_subsystems_and_suffixes_are_rejected(self):
        for command in ('query-core-outbox-v1 ' + A, 'query-core-outbox-v1; id', 'query-core-outbox-v1\nid',
                        'internal-sftp', 'scp -t /tmp/file', 'bash', ''):
            self.env['SSH_ORIGINAL_COMMAND'] = command
            self.assertEqual(self.execute().returncode, 64)
        self.assertFalse((self.root / 'timeout-call').exists())

    def test_query_obeys_existing_maintenance_and_disable_markers(self):
        self.env['SSH_ORIGINAL_COMMAND'] = 'query-core-outbox-v1'
        shell_fixtures.WrapperTests.test_lock_timeout_and_both_sentinels_stop_before_host_logic(self)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('flock'), 'Real POSIX flock required')
    def test_query_waits_for_the_real_shared_deployment_lock(self):
        self.env['SSH_ORIGINAL_COMMAND'] = 'query-core-outbox-v1'
        shell_fixtures.WrapperTests.test_real_shared_lock_blocks_until_existing_deployment_releases_it(self)


DOCKER = os.environ.get('OUTBOX_QUERY_DOCKER', shutil.which('docker') or '')


@unittest.skipUnless(os.environ.get('OUTBOX_QUERY_POSTGRES_TESTS') == '1', 'Opt-in isolated PostgreSQL fixture')
class QueryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No exposed ports, network or host mounts; only an already cached image is allowed.
        result = subprocess.run([DOCKER, 'run', '--detach', '--rm', '--pull=never', '--network', 'none',
                                 '--tmpfs', '/var/lib/postgresql/data:rw,size=256m',
                                 '--name', 'm2-outbox-query-test-' + uuid4().hex,
                                 '-e', 'POSTGRES_USER=query_fixture', '-e', 'POSTGRES_DB=query_fixture',
                                 '-e', 'POSTGRES_PASSWORD=fictional-query-test-password', 'postgres:17'],
                                text=True, capture_output=True, timeout=30, check=True)
        cls.container = result.stdout.strip()
        if len(cls.container) != 64 or not all(character in '0123456789abcdef' for character in cls.container):
            raise RuntimeError('Unexpected fixture container identifier')
        cls.addClassCleanup(lambda: subprocess.run([DOCKER, 'rm', '-f', cls.container], capture_output=True, timeout=20))
        deadline = time.monotonic() + 30
        while True:
            # Wait for the final server, not the temporary initdb bootstrap server.
            ready = subprocess.run([DOCKER, 'exec', cls.container, 'sh', '-c',
                                    'test "$(cat /proc/1/comm)" = postgres && pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
                                   capture_output=True)
            if ready.returncode == 0: break
            if time.monotonic() >= deadline: raise RuntimeError('Local PostgreSQL fixture did not start')
            time.sleep(0.5)
        seed = f"""CREATE TABLE public.outbox_events (
            event_id uuid PRIMARY KEY, ticket_id uuid NOT NULL, payload jsonb NOT NULL,
            created_at timestamptz, published_at timestamptz, status text NOT NULL,
            event_type text NOT NULL, update_type text, retry_count integer DEFAULT 0);
            INSERT INTO public.outbox_events VALUES
            ('{A}','{TICKET}','{{"correlationId":"{C}","data":{{"responsibleAreaId":"M3","citizenName":"{SECRET}"}},"token":"{SECRET}"}}',
             '2026-11-15 01:00Z','2026-11-15 02:30Z','PUBLISHED','ticketUpdated','ROUTED',1),
            ('{B}','{TICKET}','{{"data":{{}}}}','2026-11-14 01:00Z',NULL,'PENDING','ticketCreated',NULL,0),
            ('{C}','{TICKET}','{{}}',NULL,NULL,'FAILED','ticketUpdated','PROGRESS',5);
            INSERT INTO public.outbox_events (event_id,ticket_id,payload,created_at,status,event_type,update_type)
            SELECT (lpad(to_hex(value),8,'0')||'-1111-4111-8111-111111111111')::uuid,'{OTHER_TICKET}',
                '{{"data":{{"responsibleAreaId":"M5"}}}}'::jsonb,
                '2026-11-15 00:00Z'::timestamptz + value * interval '1 minute','PENDING','ticketUpdated','PROGRESS'
            FROM generate_series(1,105) AS value;
        """
        cls.psql(seed)

    @classmethod
    def psql(cls, sql, *, readonly=False, check=True):
        command = query.PSQL if readonly else 'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
        result = subprocess.run([DOCKER, 'exec', '-i', cls.container, 'sh', '-c', command], input=sql,
                                text=True, capture_output=True, timeout=15, check=False)
        if check and result.returncode:
            raise AssertionError('Isolated fixture SQL failed: ' + result.stderr)
        return result

    def execute(self, selection):
        with tempfile.TemporaryDirectory() as directory:
            host = query.Host(directory)
            host.compose_dir.mkdir()
            host.runtime.write_bytes(b'# private fixture\n'); host.runtime.chmod(0o600)
            before = host.runtime.read_bytes()
            def compose(*args, **kwargs):
                self.assertEqual(args, ('exec', '-T', 'postgres', 'sh', '-c', query.PSQL))
                return self.psql(kwargs['data'], readonly=True).stdout
            with patch.object(host, 'compose', side_effect=compose): report = query.query(host, selection)
            self.assertEqual(host.runtime.read_bytes(), before)
            return report

    def test_real_postgres_multiple_events_nulls_and_reference_timezone(self):
        report = self.execute(SELECTION)
        self.assertEqual([value['event_id'] for value in report['rows']], [A, B, C])
        self.assertEqual(report['rows'][0]['published_at_argentina'], '2026-11-14T23:30:00')
        self.assertIsNone(report['rows'][1]['correlation_id'])
        self.assertIsNone(report['rows'][1]['area'])
        self.assertIsNone(report['rows'][1]['published_at'])
        self.assertIsNone(report['rows'][2]['created_at'])
        self.assertNotIn(SECRET, json.dumps(report))
        event = self.execute({'lookup_by': 'event_id', 'lookup_id': A})
        self.assertEqual(event['rows'], report['rows'][:1])

    def test_real_postgres_limit_and_no_results(self):
        report = self.execute({'lookup_by': 'ticket_id', 'lookup_id': OTHER_TICKET})
        self.assertEqual(len(report['rows']), 100)
        self.assertTrue(report['truncated'])
        self.assertEqual(report['rows'][0]['event_id'], '00000069-1111-4111-8111-111111111111')
        self.assertEqual(self.execute({'lookup_by': 'event_id', 'lookup_id': str(uuid4())})['code'], 'NO_RESULTS')

    def test_readonly_session_rejects_writes_and_all_outbox_records_remain_unchanged(self):
        checksum = "SELECT md5(string_agg(row_to_json(value)::text, '' ORDER BY event_id)) FROM public.outbox_events AS value;"
        before = self.psql(checksum).stdout
        self.execute(SELECTION)
        self.execute({'lookup_by': 'ticket_id', 'lookup_id': OTHER_TICKET})
        self.assertEqual(self.psql('SHOW default_transaction_read_only;', readonly=True).stdout.strip(), 'on')
        denied = self.psql(query.sql_for(SELECTION).replace('COMMIT;', "UPDATE public.outbox_events SET status='FAILED'; COMMIT;"), readonly=True, check=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('read-only transaction', denied.stderr)
        self.assertEqual(self.psql(checksum).stdout, before)


if __name__ == '__main__':
    unittest.main()
