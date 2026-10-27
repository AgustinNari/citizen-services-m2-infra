"""Optional real proxy tests. Set CADDY_BIN, NGINX_BIN and FRONTEND_NGINX_CONFIG.

Uses only loopback, ephemeral ports and an echo backend; never contacts ACME/AWS.
The real Caddyfile is adapted to HTTP solely to exercise forwarding locally.
Public certificate issuance is deliberately outside this test's scope.
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
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
        self.end_headers()
        self.wfile.write(data)

    do_POST = do_PUT = do_PATCH = do_GET

    def log_message(self, *_args):
        pass


@unittest.skipUnless(all(os.environ.get(x) for x in ('CADDY_BIN', 'NGINX_BIN', 'FRONTEND_NGINX_CONFIG')),
                     'Optional: provide local Caddy/Nginx binaries and frontend config')
class ProxyRuntimeTests(unittest.TestCase):
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
        cls.host = '54-232-233-237.sslip.io'
        config = Path(os.environ['FRONTEND_NGINX_CONFIG']).read_text()
        config = config.replace('8082', str(cls.private_port))
        config = config.replace('listen 80;', f'listen 127.0.0.1:{cls.local_port};')
        config = config.replace(f'listen {cls.private_port};', f'listen 127.0.0.1:{cls.private_port};')
        config = config.replace('http://backend:8080', f'http://127.0.0.1:{cls.backend.server_port}')
        config = config.replace('/usr/share/nginx/html', (root / 'html').as_posix())
        (root / 'nginx.conf').write_text('worker_processes 1;\nevents {}\nhttp {\n' + config + '\n}')
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
        caddy = caddy.replace('{\n', '{\n\tadmin off\n', 1)
        caddy = caddy.replace('{$HTTPS_HOSTNAME} {', f'http://127.0.0.1:{cls.edge_port} {{', 1)
        caddy = re.sub(r'\ttls \{.*?\n\t\}', '', caddy, count=1, flags=re.S)
        caddy = caddy.replace('frontend:8082', f'127.0.0.1:{cls.private_port}')
        caddy = caddy.replace('http://{$LEGACY_HTTP_IP}', f'http://127.0.0.1:{cls.legacy_port}')
        (root / 'Caddyfile').write_text(caddy)
        env = os.environ.copy()
        env['HTTPS_HOSTNAME'] = cls.host
        process = subprocess.Popen([os.environ['CADDY_BIN'], 'run', '--config', str(root / 'Caddyfile'),
                                    '--adapter', 'caddyfile'], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop, process)
        for selected_port in (cls.local_port, cls.private_port, cls.edge_port):
            deadline = time.monotonic() + 10
            while True:
                try:
                    with socket.create_connection(('127.0.0.1', selected_port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Proxy did not start')
                    time.sleep(0.1)

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

    def request(self, selected_port, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', selected_port, timeout=10)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

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

    def test_api_redirect_and_react_route(self):
        status, headers, _ = self.request(self.edge_port, 'POST', '/api?a=%2F&b=1', b'keep-body')
        self.assertEqual((status, headers['Location']), (308, '/api/?a=%2F&b=1'))
        self.assertEqual(self.request(self.edge_port, 'GET', '/tickets/123')[0], 200)

    def test_legacy_ip_redirect_preserves_uri(self):
        status, headers, _ = self.request(self.legacy_port, 'POST', '/api/test?a=%2F', b'keep-body')
        self.assertEqual((status, headers['Location']), (308, f'https://{self.host}/api/test?a=%2F'))

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
