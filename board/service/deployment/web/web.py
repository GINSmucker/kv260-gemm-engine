"""Unprivileged LAN HTTP service. It cannot open XRT or control firmware."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys
from common import MAX_IMAGE, RUN, encoded, need, receive, send

HERE = Path(__file__).absolute().parent
HOST = '192.168.137.2:8080'
ORIGIN = 'http://' + HOST


def backend(action, token=None, body=b''):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(5)
        stream.connect(str(RUN / 'backend.sock'))
        send(stream, {'action': action, 'request_id': token}, body)
        response, extra = receive(stream)
        need(not extra, 'unexpected API body')
        return response


class Handler(BaseHTTPRequestHandler):
    server_version = 'OPT50-Demo/1'
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def write(self, code, raw, kind='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        # C's chart components generate bounded numeric style attributes for
        # bars and tooltips. Inline scripts remain forbidden.
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def allowed(self):
        ip = ipaddress.ip_address(self.client_address[0])
        return (ip.is_loopback or ip in ipaddress.ip_network('192.168.137.0/24')) and self.headers.get('Host') == HOST

    def do_GET(self):
        if not self.allowed():
            return self.write(403, encoded({'error': 'LAN host policy'}))
        try:
            if self.path == '/api/status':
                try:
                    value = backend('status')
                except OSError:
                    path = RUN / 'status.json'
                    saved = json.loads(path.read_bytes()) if path.exists() else {}
                    value = {'ok': True, 'value': {'stage': 'BACKEND_UNAVAILABLE', 'last_saved_stage': saved.get('stage'),
                                                'saved_status_only': True, 'boot_id': saved.get('boot_id')}}
                return self.write(200, encoded(value))
            if re.fullmatch('/api/result/[0-9a-f]{32}', self.path):
                return self.write(200, encoded(backend('result', self.path.rsplit('/', 1)[1])))
            if self.path == '/api/history':
                return self.write(200, encoded(backend('history')))
            mapping = {'/': ('index.html', 'text/html; charset=utf-8'),
                       '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                       '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
                       '/tokens.css': ('tokens.css', 'text/css; charset=utf-8'),
                       '/sparse24.css': ('sparse24.css', 'text/css; charset=utf-8'),
                       '/api-client.js': ('api-client.js', 'text/javascript; charset=utf-8'),
                       '/sparse24-api.js': ('sparse24-api.js', 'text/javascript; charset=utf-8'),
                       '/live-view.js': ('live-view.js', 'text/javascript; charset=utf-8'),
                       '/presentation.js': ('presentation.js', 'text/javascript; charset=utf-8'),
                       '/charts.js': ('charts.js', 'text/javascript; charset=utf-8'),
                       '/metrics.json': ('metrics.json', 'application/json; charset=utf-8'),
                       '/board500.json': ('board500.json', 'application/json; charset=utf-8'),
                       '/paired195_cpu_pl.csv': ('paired195_cpu_pl.csv', 'text/csv; charset=utf-8'),
                       '/ui_provenance.json': ('ui_provenance.json', 'application/json; charset=utf-8')}
            for asset in ('bridge.svg', 'dog.svg', 'pattern.svg'):
                mapping['/assets/'+asset] = ('assets/'+asset, 'image/svg+xml')
            if self.path in mapping:
                name, kind = mapping[self.path]
                return self.write(200, (HERE / 'static' / name).read_bytes(), kind)
            return self.write(404, encoded({'error': 'not found'}))
        except (OSError, ValueError) as error:
            return self.write(503, encoded({'error': 'backend unavailable', 'detail': str(error)}))

    def do_POST(self):
        if not self.allowed() or self.headers.get('Origin') != ORIGIN:
            return self.write(403, encoded({'error': 'same-origin browser request required'}))
        if not re.fullmatch('/api/infer/[0-9a-f]{32}', self.path):
            return self.write(404, encoded({'error': 'not found'}))
        try:
            need(not self.headers.get('Transfer-Encoding') and len(self.headers.get_all('Content-Length', [])) == 1,
                 'one Content-Length required')
            length = int(self.headers.get('Content-Length', '0'))
            need(0 < length <= MAX_IMAGE and self.headers.get('Content-Type') == 'image/jpeg', 'JPEG must be 1..8 MiB')
            raw = self.rfile.read(length)
            need(len(raw) == length, 'truncated JPEG')
            response = backend('infer', self.path.rsplit('/', 1)[1], raw)
            return self.write(202 if response.get('ok') else 409, encoded(response))
        except (ValueError, OSError) as error:
            return self.write(400, encoded({'error': str(error), 'automatic_replay': False}))


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', 8080), Handler)
    server.daemon_threads = True
    server.serve_forever()
