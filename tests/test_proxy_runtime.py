"""Optional real proxy tests. Set CADDY_BIN, NGINX_BIN and FRONTEND_NGINX_CONFIG.

Uses only loopback, ephemeral ports, a disposable TLS CA and an echo backend.
Public certificate issuance is deliberately outside this test's scope.
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest


def port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if '/attachments/' in self.path:
            data = b'%PDF-1.7\nattachment-test\x00\xff'
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Disposition', 'attachment; filename="test.pdf"')
        else:
            data = json.dumps({'method': self.command, 'path': self.path,
                               'body': body.hex(), 'headers': dict(self.headers)}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Security-Policy', "default-src 'none'")
        self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        self.end_headers()
        self.wfile.write(data)

    do_POST = do_PUT = do_PATCH = do_GET

    def log_message(self, *_args):
        pass


@unittest.skipUnless(all(os.environ.get(x) for x in ('CADDY_BIN', 'NGINX_BIN', 'FRONTEND_NGINX_CONFIG')),
                     'Optional: provide local Caddy/Nginx binaries and frontend config')
class ProxyRuntimeTests(unittest.TestCase):
    HOSTNAME = '54-232-233-237.sslip.io'

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        (root / 'logs').mkdir()
        (root / 'temp').mkdir()
        (root / 'html').mkdir()
        (root / 'html/index.html').write_text('<html>React runtime fixture</html>')
        cls.backend = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
        cls.addClassCleanup(cls.backend.server_close)
        cls.addClassCleanup(cls.backend.shutdown)
        threading.Thread(target=cls.backend.serve_forever, daemon=True).start()
        cls.local_port, cls.private_port, cls.edge_port, cls.legacy_port = [port() for _ in range(4)]
        cls.host = cls.HOSTNAME
        config = Path(os.environ['FRONTEND_NGINX_CONFIG']).read_text(encoding='utf-8')
        config = config.replace('8082', str(cls.private_port))
        config = config.replace('listen 80;', f'listen 127.0.0.1:{cls.local_port};')
        config = config.replace(f'listen {cls.private_port};', f'listen 127.0.0.1:{cls.private_port};')
        config = config.replace('http://backend:8080', f'http://127.0.0.1:{cls.backend.server_port}')
        config = config.replace('/usr/share/nginx/html', (root / 'html').as_posix())
        (root / 'nginx.conf').write_text('worker_processes 1;\nevents {}\nhttp {\n' + config + '\n}', encoding='utf-8')
        prefix = root.as_posix() + '/'
        check = subprocess.run([os.environ['NGINX_BIN'], '-p', prefix, '-c', 'nginx.conf', '-t'],
                               capture_output=True, text=True)
        if check.returncode:
            raise RuntimeError(check.stderr)
        nginx = subprocess.Popen([os.environ['NGINX_BIN'], '-p', prefix, '-c', 'nginx.conf',
                                  '-g', 'daemon off; master_process off;'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop, nginx)
        caddy = (Path(__file__).resolve().parents[1] / 'caddy/Caddyfile').read_text()
        caddy = caddy.replace('{\n', '{\n\tadmin off\n\tskip_install_trust\n\tauto_https disable_redirects\n', 1)
        caddy = caddy.replace('{$HTTPS_HOSTNAME} {', f'https://127.0.0.1:{cls.edge_port} {{', 1)
        caddy = re.sub(r'\ttls \{.*?\n\t\}', '\ttls internal', caddy, count=1, flags=re.S)
        caddy = caddy.replace('frontend:8082', f'127.0.0.1:{cls.private_port}')
        caddy = caddy.replace('http://{$LEGACY_HTTP_IP}', f'http://127.0.0.1:{cls.legacy_port}')
        (root / 'Caddyfile').write_text(caddy)
        env = os.environ.copy()
        env['HTTPS_HOSTNAME'] = cls.host
        env['XDG_DATA_HOME'] = str(root / 'data')
        process = subprocess.Popen([os.environ['CADDY_BIN'], 'run', '--config', str(root / 'Caddyfile'),
                                    '--adapter', 'caddyfile'], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop, process)
        root_ca = root / 'data/caddy/pki/authorities/local/root.crt'
        for selected_port in (cls.local_port, cls.private_port, cls.edge_port):
            deadline = time.monotonic() + 10
            while True:
                try:
                    with socket.create_connection(('127.0.0.1', selected_port), timeout=0.5) as connection:
                        if selected_port == cls.edge_port:
                            cls.tls = ssl.create_default_context(cafile=str(root_ca))
                            with cls.tls.wrap_socket(connection, server_hostname='127.0.0.1'):
                                pass
                    break
                except OSError as error:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Proxy did not start') from error
                    time.sleep(0.1)

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

    def request(self, selected_port, method, path, body=None, headers=None):
        if selected_port == self.edge_port:
            connection = http.client.HTTPSConnection('127.0.0.1', selected_port, timeout=10, context=self.tls)
        else:
            connection = http.client.HTTPConnection('127.0.0.1', selected_port, timeout=10)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.headers, response.read()

    def test_methods_bodies_queries_and_sanitized_headers(self):
        for method in ('POST', 'PUT', 'PATCH'):
            with self.subTest(method=method):
                payload = b'--boundary\r\nContent-Disposition: form-data; name="file"\r\n\r\n\x00\xff\r\n--boundary--'
                status, _, body = self.request(self.edge_port, method, '/api/echo?a=%2F&b=1', payload, {
                    'Content-Type': 'multipart/form-data; boundary=boundary',
                    'X-Forwarded-Proto': 'http', 'X-Forwarded-Host': 'attacker.invalid',
                    'X-Forwarded-For': '203.0.113.99', 'X-Forwarded-Port': '1234',
                    'Forwarded': 'proto=http;host=attacker.invalid', 'X-Forwarded-Prefix': '/evil'})
                self.assertEqual(status, 200)
                echo = json.loads(body)
                self.assertEqual((echo['method'], echo['path'], echo['body']),
                                 (method, '/api/echo?a=%2F&b=1', payload.hex()))
                headers = {k.lower(): v for k, v in echo['headers'].items()}
                for key, value in {'host': self.host, 'x-forwarded-host': self.host,
                                   'x-forwarded-proto': 'https', 'x-forwarded-port': '443',
                                   'x-forwarded-for': '127.0.0.1', 'x-real-ip': '127.0.0.1'}.items():
                    self.assertEqual(headers[key], value)
                self.assertNotIn('forwarded', headers)
                self.assertNotIn('x-forwarded-prefix', headers)

    def test_local_http_does_not_trust_incoming_proxy_headers(self):
        status, _, body = self.request(self.local_port, 'GET', '/api/echo', headers={
            'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '203.0.113.99'})
        self.assertEqual(status, 200)
        headers = {k.lower(): v for k, v in json.loads(body)['headers'].items()}
        self.assertEqual(headers['x-forwarded-proto'], 'http')
        self.assertEqual(headers['x-forwarded-for'], '127.0.0.1')

    def test_tracking_limit_cannot_be_reset_with_forwarded_headers(self):
        # Echo upstream never returns 429: this exercises Nginx's independent gate.
        for attempt in range(9):
            status, _, body = self.request(self.edge_port, 'POST', '/api/tracking/access', b'{}', {
                'Content-Type': 'application/json',
                'X-Forwarded-For': f'198.51.100.{attempt + 1}',
                'Forwarded': f'for=203.0.113.{attempt + 1}'})
            self.assertEqual(status, 200)
            headers = {k.lower(): v for k, v in json.loads(body)['headers'].items()}
            self.assertEqual(headers['x-forwarded-for'], '127.0.0.1')
            self.assertNotIn('forwarded', headers)
        for forged in ('203.0.113.99', '192.0.2.1, 198.51.100.99', '2001:db8::7'):
            status, headers, body = self.request(self.edge_port, 'POST', '/api/tracking/access', b'{}', {
                'Content-Type': 'application/json', 'X-Forwarded-For': forged})
            self.assertEqual(status, 429)
            self.assertEqual(headers['Content-Type'], 'application/problem+json')
            self.assertEqual(headers['Retry-After'], '60')
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertIsNone(headers['Content-Security-Policy'])
            self.assertEqual(headers.get_all('Strict-Transport-Security'), ['max-age=86400'])
            problem = json.loads(body)
            self.assertEqual(problem['status'], status)
            self.assertEqual(problem['type'], 'about:blank')
            self.assertEqual(problem['title'], 'Too Many Requests')
            self.assertEqual(problem['code'], 'TOO_MANY_ATTEMPTS')
            self.assertEqual(problem['detail'], 'Demasiados intentos. Esperá un minuto y volvé a intentar.')
            self.assertEqual(problem['message'], problem['detail'])

    def test_api_redirect_and_react_route(self):
        status, headers, _ = self.request(self.edge_port, 'POST', '/api?a=%2F&b=1', b'keep-body')
        self.assertEqual((status, headers['Location']), (308, '/api/?a=%2F&b=1'))
        self.assertEqual(self.request(self.edge_port, 'GET', '/tickets/123')[0], 200)

    def test_legacy_ip_redirect_preserves_uri(self):
        status, headers, _ = self.request(self.legacy_port, 'POST', '/api/test?a=%2F', b'keep-body')
        self.assertEqual((status, headers['Location']), (308, f'https://{self.host}/api/test?a=%2F'))
        self.assertIsNone(headers['Strict-Transport-Security'])

    def test_portal_and_api_security_headers(self):
        for path in ('/', '/tickets/123', '/api/echo'):
            for selected_port in (self.edge_port, self.local_port):
                with self.subTest(path=path, port=selected_port):
                    status, headers, _ = self.request(selected_port, 'GET', path)
                    self.assertEqual(status, 200)
                    self.assertIn('nosniff', headers.get_all('X-Content-Type-Options', []))
                    self.assertEqual(headers['X-Frame-Options'], 'DENY')
                    self.assertEqual(headers['Referrer-Policy'], 'strict-origin-when-cross-origin')
                    expected_hsts = ['max-age=86400'] if selected_port == self.edge_port else []
                    self.assertEqual(headers.get_all('Strict-Transport-Security', []), expected_hsts)
                    policies = headers.get_all('Content-Security-Policy', [])
                    self.assertEqual(len(policies), 1)
                    if path.startswith('/api/'):
                        self.assertEqual(policies[0], "default-src 'none'")
                    else:
                        self.assertIn("frame-ancestors 'none'", policies[0])

    def test_body_limit_and_attachment_download(self):
        # Check Nginx's rejection at headers, without sending 55 MiB through Go's
        # request-body drain logic. Actual multipart forwarding is tested above.
        status, _, _ = self.request(self.private_port, 'POST', '/api/echo', headers={
            'Content-Length': str(55 * 1024 * 1024 + 1)})
        self.assertEqual(status, 413)
        status, headers, body = self.request(self.edge_port, 'GET', '/api/attachments/1/content')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'%PDF-1.7\nattachment-test\x00\xff')
        self.assertEqual(headers['Content-Disposition'], 'attachment; filename="test.pdf"')


class DuckDnsProxyRuntimeTests(ProxyRuntimeTests):
    """Exercise the same real forwarding checks without resolving DuckDNS."""
    HOSTNAME = 'atencion-ciudadana.duckdns.org'


class DynuProxyRuntimeTests(ProxyRuntimeTests):
    """Reuse real proxy checks with the definitive hostname; no public DNS/ACME."""
    HOSTNAME = 'atencion-ciudadana.ddnsfree.com'
