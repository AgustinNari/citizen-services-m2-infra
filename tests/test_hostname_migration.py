"""Offline state-machine tests: no Docker daemon, DNS changes, ACME or AWS."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import shutil
import socket
import ssl
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hostname_migration', ROOT / 'scripts/migrate_https_hostname.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
OLD = '54-232-233-237.sslip.io'
NEW = 'atencion-ciudadana.ddnsfree.com'
IP = '54.232.233.237'


class FakeDocker:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.fail_up = None
        self.fail_config = False
        self.fail_caddy_validation = False
        self.missing_image = False
        self.containers = {}
        self.counter = 0
        for name in m.SERVICES:
            self.containers[name] = {
                'Id': name + '-original', 'Image': 'sha256:' + name,
                'Config': {'Image': name + ':cached', 'Env': ['PATH=/bin'], 'Labels': {
                    'com.docker.compose.service': name, 'com.docker.compose.project': 'existing-project',
                    'com.docker.compose.project.working_dir': str(root / 'compose')}},
                'State': {'Running': True},
                'Mounts': [{'Type': 'volume', 'Name': name + '_retained', 'Source': '/volumes/' + name,
                            'Destination': '/data', 'RW': True}]}
            self.refresh(name)

    def config(self, env=None):
        runtime = (self.root / 'compose/.env').read_text()
        host = runtime.split('HTTPS_HOSTNAME=')[1].splitlines()[0]
        simulator = runtime.split('SIMULATOR_ENABLED=')[1].splitlines()[0]
        host = (env or {}).get('HTTPS_HOSTNAME', host)
        result = {'name': 'existing-project', 'services': {
            'backend': {'environment': {'M2_PUBLIC_BASE_URL': 'https://' + host,
                                        'SIMULATOR_ENABLED': simulator, 'PASSWORD': 'dummy-secret'}},
            'caddy': {'environment': {'HTTPS_HOSTNAME': host, 'LEGACY_HTTP_IP': IP}},
            'frontend': {}, 'postgres': {}}, 'volumes': {'persistent': {}}, 'networks': {'edge': {}}}
        for name in m.SERVICES:
            result['services'][name]['image'] = name + ':cached'
        return result

    def refresh(self, name):
        values = self.config()['services'][name].get('environment', {})
        self.containers[name]['Config']['Env'] = ['PATH=/bin'] + [k + '=' + v for k, v in values.items()]

    def __call__(self, *args, env=None):
        self.calls.append(args)
        if args[:2] == ('docker', 'ps'):
            return self.containers['backend']['Id'] + '\n'
        if args[:2] == ('docker', 'inspect'):
            return json.dumps([next(x for x in self.containers.values() if x['Id'] == args[2])])
        if args[:3] == ('docker', 'image', 'inspect'):
            if self.missing_image:
                raise m.MigrationError('image unavailable')
            return next(x['Image'] for x in self.containers.values() if x['Config']['Image'] == args[-1])
        if args[:2] == ('docker', 'run'):
            if self.fail_caddy_validation:
                raise m.MigrationError('Caddy validation failed')
            return ''
        if args[:2] == ('docker', 'compose'):
            if 'config' in args:
                if self.fail_config and NEW in (self.root / 'compose/.env').read_text():
                    raise m.MigrationError('Compose failed')
                return '' if '--quiet' in args else json.dumps(self.config(env))
            if 'ps' in args:
                return self.containers[args[-1]]['Id']
            if 'up' in args:
                name = args[-1]
                if name == self.fail_up:
                    self.fail_up = None
                    raise m.MigrationError('up failed')
                self.counter += 1
                self.containers[name]['Id'] = name + '-recreated-' + str(self.counter)
                self.refresh(name)
                return ''
        if args[0] == 'bash':
            return 'HTTPS smoke passed'
        raise AssertionError('Unexpected command: ' + str(args))

    def ups(self):
        return [args[-1] for args in self.calls if args[:2] == ('docker', 'compose') and 'up' in args]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'infra'
        self.state = Path(self.temp.name) / 'state'
        self.state.mkdir(mode=0o700)
        for relative in m.INPUTS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('unchanged fixture\n')
        self.original = ('HTTPS_HOSTNAME=' + OLD + '\r\nSIMULATOR_ENABLED=true\r\n'
                         'COMPOSE_PROJECT_NAME=existing-project\r\nPASSWORD=dummy-secret\r\n').encode()
        (self.root / 'compose/.env').write_bytes(self.original)
        self.docker = FakeDocker(self.root)
        self.tls_failure = False
        self.smoke_failure = False

        def tls(host, minimum=0):
            if self.tls_failure and host == NEW:
                raise m.MigrationError('untrusted TLS')

        self.migration = m.Migration(self.root, self.state, run=self.docker,
                                     dns=lambda host, ip: [{'tested': host}], tls=tls,
                                     sleep=lambda _: None)
        original_smoke = self.migration.smoke

        def smoke(host):
            if self.smoke_failure and host == NEW:
                raise m.MigrationError('smoke failed')
            return original_smoke(host)

        self.migration.smoke = smoke

    def backup(self):
        path = Path((self.state / 'hostname-last-backup').read_text().strip())
        return self.migration.load(path)

    def test_preflight_is_read_only_and_validates_caddy_without_network(self):
        before = copy.deepcopy(self.docker.containers)
        record = self.migration.preflight(NEW)
        self.assertEqual(record['project'], 'existing-project')
        self.assertEqual(before, self.docker.containers)
        self.assertEqual((self.root / 'compose/.env').read_bytes(), self.original)
        self.assertFalse(self.migration.marker.exists())
        self.assertEqual(self.docker.ups(), [])
        validation = next(x for x in self.docker.calls if x[:2] == ('docker', 'run'))
        self.assertIn('none', validation)
        self.assertIn('never', validation)
        self.assertFalse(any('/data' in arg for arg in validation))

    def test_apply_preserves_runtime_images_mounts_and_keeps_maintenance_until_finish(self):
        before = copy.deepcopy(self.docker.containers)
        self.migration.apply(NEW)
        backup, record = self.backup()
        self.assertEqual(self.docker.ups(), ['caddy', 'backend'])
        for name in ('postgres', 'frontend'):
            self.assertEqual(before[name], self.docker.containers[name])
        self.assertEqual((self.root / 'compose/.env').read_bytes(), self.original.replace(OLD.encode(), NEW.encode()))
        self.assertEqual((backup / 'compose/.env').read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((backup / 'state.private.json').stat().st_mode), 0o600)
        self.assertEqual(record['phase'], 'applied')
        self.migration.owner(backup)
        self.migration.finish(backup, record)
        self.assertFalse(self.migration.marker.exists())
        self.migration.finish(backup, record)

    def test_apply_idempotent_when_already_using_target(self):
        self.migration.apply(OLD)
        self.assertEqual(self.docker.ups(), [])
        self.assertFalse(self.migration.marker.exists())
        self.assertFalse((self.state / 'hostname-last-backup').exists())

    def test_disabled_simulator_is_preserved_during_apply_and_rollback(self):
        self.original = self.original.replace(b'SIMULATOR_ENABLED=true', b'SIMULATOR_ENABLED=false')
        (self.root / 'compose/.env').write_bytes(self.original)
        self.docker.refresh('backend')
        self.migration.apply(NEW)
        backup, record = self.backup()
        self.assertIn('SIMULATOR_ENABLED=false', self.docker.containers['backend']['Config']['Env'])
        self.migration.rollback(backup, record)
        self.assertEqual((self.root / 'compose/.env').read_bytes(), self.original)

    def test_existing_maintenance_is_never_taken_over(self):
        self.migration.marker.write_text('another operation')
        with self.assertRaises(m.MigrationError):
            self.migration.apply(NEW)
        self.assertEqual(self.migration.marker.read_text(), 'another operation')
        self.assertEqual(self.docker.ups(), [])

    def test_dns_cache_and_caddy_validation_fail_before_any_runtime_edit(self):
        for fault in ('dns', 'image', 'caddy'):
            with self.subTest(fault=fault):
                original_dns = self.migration.dns
                self.migration.dns = (lambda *_: (_ for _ in ()).throw(m.MigrationError('DNS failed'))) if fault == 'dns' else original_dns
                self.docker.missing_image = fault == 'image'
                self.docker.fail_caddy_validation = fault == 'caddy'
                with self.assertRaises(m.MigrationError):
                    self.migration.apply(NEW)
                self.migration.dns = original_dns
                self.assertEqual((self.root / 'compose/.env').read_bytes(), self.original)
                self.assertFalse(self.migration.marker.exists())
                self.assertEqual(self.docker.ups(), [])

    def test_new_tls_failure_stops_before_backend_and_supports_explicit_rollback(self):
        self.tls_failure = True
        with self.assertRaises(m.MigrationError):
            self.migration.apply(NEW)
        backup, record = self.backup()
        self.assertEqual(record['phase'], 'failed')
        self.assertEqual(self.docker.ups(), ['caddy'])
        self.migration.rollback(backup, record)
        self.assertEqual(self.docker.ups(), ['caddy', 'caddy'])
        self.assertEqual((self.root / 'compose/.env').read_bytes(), self.original)
        self.migration.finish(backup, record)

    def test_compose_failure_before_recreation_can_rollback_without_restarts(self):
        self.docker.fail_config = True
        with self.assertRaises(m.MigrationError):
            self.migration.apply(NEW)
        backup, record = self.backup()
        self.assertEqual(self.docker.ups(), [])
        self.docker.fail_config = False
        self.migration.rollback(backup, record)
        self.assertEqual(self.docker.ups(), [])

    def test_failed_up_and_smoke_preserve_backup_and_rollback_both_services(self):
        for fault in ('up', 'smoke'):
            with self.subTest(fault=fault):
                self.docker.fail_up = 'backend' if fault == 'up' else None
                self.smoke_failure = fault == 'smoke'
                with self.assertRaises(m.MigrationError):
                    self.migration.apply(NEW)
                backup, record = self.backup()
                self.assertEqual(record['attempted'], ['caddy', 'backend'])
                self.smoke_failure = False
                self.migration.rollback(backup, record)
                calls = len(self.docker.ups())
                self.migration.rollback(backup, record)
                self.assertEqual(len(self.docker.ups()), calls)
                self.migration.finish(backup, record)

    def test_finished_cutover_can_be_rolled_back_in_a_new_maintenance_window(self):
        self.migration.apply(NEW)
        backup, record = self.backup()
        self.migration.finish(backup, record)
        self.migration.rollback(backup, record)
        self.assertEqual(record['phase'], 'rolled-back')
        self.migration.finish(backup, record)
        self.assertEqual(record['accepted_host'], OLD)

    def test_mount_drift_unrelated_edits_and_foreign_marker_are_rejected(self):
        self.migration.apply(NEW)
        backup, record = self.backup()
        self.docker.containers['backend']['Mounts'][0]['Name'] = 'wrong-volume'
        with self.assertRaises(m.MigrationError):
            self.migration.finish(backup, record)
        self.assertTrue(self.migration.marker.exists())
        (self.root / 'compose/.env').write_bytes(self.original + b'UNRELATED=preserve\n')
        with self.assertRaises(m.MigrationError):
            self.migration.rollback(backup, record)
        self.assertIn(b'UNRELATED=preserve', (self.root / 'compose/.env').read_bytes())
        self.migration.marker.write_text('other owner')
        with self.assertRaises(m.MigrationError):
            self.migration.rollback(backup, record)

    def test_project_mismatch_and_ambiguous_selection_refused(self):
        self.docker.containers['backend']['Config']['Labels']['com.docker.compose.project'] = 'other-project'
        with self.assertRaises(m.MigrationError):
            self.migration.preflight(NEW)
        self.assertEqual(self.docker.ups(), [])

    def test_pending_image_reference_change_is_rejected_before_recreation(self):
        self.docker.containers['backend']['Config']['Image'] = 'backend:another-sha'
        with self.assertRaises(m.MigrationError):
            self.migration.apply(NEW)
        self.assertEqual(self.docker.ups(), [])
        self.assertFalse(self.migration.marker.exists())

    def test_rollback_refuses_mount_drift_before_recreating_anything(self):
        self.migration.apply(NEW)
        backup, record = self.backup()
        self.docker.containers['backend']['Mounts'][0]['Name'] = 'foreign-volume'
        count = len(self.docker.ups())
        with self.assertRaises(m.MigrationError):
            self.migration.rollback(backup, record)
        self.assertEqual(len(self.docker.ups()), count)

    def test_rollback_can_recover_a_stopped_backend_after_a_partial_failure(self):
        self.migration.apply(NEW)
        backup, record = self.backup()
        self.docker.containers['backend']['State']['Running'] = False
        original = self.docker.refresh
        def start(name):
            self.docker.containers[name]['State']['Running'] = True
            original(name)
        self.docker.refresh = start
        self.migration.rollback(backup, record)
        self.assertEqual(record['phase'], 'rolled-back')


class DnsAndInputTests(unittest.TestCase):
    def test_dns_checks_both_resolvers_and_all_ancestors(self):
        seen = []
        def fetch(resolver, name, kind):
            seen.append((resolver, name, kind))
            return {'Status': 0, 'Answer': [{'type': 1, 'data': IP}] if kind == 'A' else []}
        result = m.check_dns(NEW, IP, fetch)
        self.assertEqual(len(result), 10)
        self.assertEqual({name for _, name, kind in seen if kind == 'CAA'}, {NEW, 'ddnsfree.com', 'com'})

    def test_dns_rejects_servfail_wrong_rr_ipv6_and_wrong_ip(self):
        for kind, bad in [('CAA', {'Status': 2}), ('CAA', {'Status': 0, 'Answer': [{'type': 1, 'data': IP}]}),
                          ('CAA', {'Status': 0, 'Answer': [{'type': 257, 'data': '0 issue "other-ca"'}]}),
                          ('AAAA', {'Status': 0, 'Answer': [{'type': 28, 'data': '::1'}]}),
                          ('A', {'Status': 0, 'Answer': [{'type': 1, 'data': '203.0.113.1'}]})]:
            with self.subTest(kind=kind, bad=bad):
                def fetch(resolver, name, query):
                    return bad if query == kind else {'Status': 0, 'Answer': [{'type': 1, 'data': IP}] if query == 'A' else []}
                with self.assertRaises(m.MigrationError):
                    m.check_dns(NEW, IP, fetch)

    def test_hostname_edit_preserves_other_bytes_and_rejects_ambiguous_input(self):
        for value in ('true', 'false'):
            before = ('# fixture\r\nexport HTTPS_HOSTNAME = ' + OLD + '\r\nSIMULATOR_ENABLED=' + value + '\r\nPASSWORD=dummy\r\n').encode()
            self.assertEqual(m.edit_hostname(before, NEW), before.replace(OLD.encode(), NEW.encode()))
        for data in (b'NO_KEY=1\n', b'HTTPS_HOSTNAME=one\nHTTPS_HOSTNAME=two\n'):
            with self.assertRaises(m.MigrationError):
                m.edit_hostname(data, NEW)
        for invalid in ('https://' + NEW, '127.0.0.1', NEW + '/path', '-bad.example.com'):
            with self.assertRaises(m.MigrationError):
                m.hostname(invalid)

    def test_runner_never_prints_captured_runtime_secrets(self):
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess(['docker'], 1, 'dummy-secret', 'dummy-secret')):
            with self.assertRaises(m.MigrationError) as raised:
                m.Runner()('docker', 'compose')
            self.assertNotIn('dummy-secret', str(raised.exception))

    def test_cli_requires_explicit_authorization_before_creating_a_migration(self):
        state = str(Path.home() / 'DA2/.deploy')
        for arguments in [('apply', '--hostname', NEW), ('rollback',), ('finish', '--accept')]:
            with self.subTest(arguments=arguments), patch.object(m, 'Migration') as migration:
                with patch.object(sys, 'argv', ['migration', '--state-dir', state, *arguments]), \
                        patch.dict(os.environ, {'M2_HOSTNAME_LOCK_HELD': '1'}):
                    with self.assertRaises(m.MigrationError):
                        m.main()
                migration.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('flock'), 'Requires Linux flock')
class SharedLockTests(unittest.TestCase):
    def test_shared_lock_blocks_other_operations_and_is_released_on_exit(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / 'migrate-https-hostname.sh'
            source = (ROOT / 'scripts/migrate-https-hostname.sh').read_text()
            source = source.replace('lock_dir="$HOME/DA2/.deploy"', 'lock_dir="' + str(root / 'state') + '"')
            source = source.replace('-w 30', '-w 0')
            wrapper.write_text(source)
            (root / 'migrate_https_hostname.py').write_text('''import fcntl, pathlib, sys
path = pathlib.Path(sys.argv[sys.argv.index('--state-dir') + 1]) / 'm2-deploy.lock'
with path.open('a') as handle:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('shared lock held')
    else:
        raise SystemExit('wrapper did not retain the lock')
''')
            (root / 'state').mkdir()
            with (root / 'state/m2-deploy.lock').open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                result = subprocess.run(['bash', str(wrapper), 'preflight'], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('shared lock held', result.stdout)
                fcntl.flock(handle, fcntl.LOCK_UN)
                result = subprocess.run(['bash', str(wrapper), 'preflight'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('shared lock held', result.stdout)
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


@unittest.skipUnless(shutil.which('openssl'), 'Requires OpenSSL for an ephemeral local certificate')
class ControlledTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        cls.cert, key = root / 'fixture.pem', root / 'fixture.key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cls.cert), '-days', '2',
                        '-subj', '/CN=' + NEW, '-addext', 'subjectAltName=DNS:' + NEW],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)

    def test_real_tls_chain_hostname_and_expiry_checks(self):
        trusted = ssl.create_default_context(cafile=str(self.cert))
        default = ssl.create_default_context()
        original_connect = socket.create_connection
        def connect(address, **kwargs):
            self.assertEqual(address, ('127.0.0.1', 443))
            return original_connect(('127.0.0.1', self.server.server_port), **kwargs)
        with patch.object(m.socket, 'create_connection', side_effect=connect):
            with patch.object(m.ssl, 'create_default_context', return_value=trusted):
                m.verify_tls(NEW)
                with self.assertRaises(ssl.SSLCertVerificationError):
                    m.verify_tls('wrong.example.com')
                with self.assertRaises(m.MigrationError):
                    m.verify_tls(NEW, 3 * 86400)
            with patch.object(m.ssl, 'create_default_context', return_value=default):
                with self.assertRaises(ssl.SSLCertVerificationError):
                    m.verify_tls(NEW)


if __name__ == '__main__':
    unittest.main()
