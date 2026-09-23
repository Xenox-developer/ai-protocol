"""Independent, read-only support API backed by local fixtures. No protocol awareness."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from local_http_server import LocalHTTPServer

TRACE_LOCK = threading.Lock()
TICKETS = json.loads((Path(__file__).parent / 'support_tickets.json').read_text())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        params = parse_qs(url.query, keep_blank_values=True)
        if trace := os.environ.get('SUPPORT_TRACE_PATH'):
            with TRACE_LOCK, open(trace, 'a') as output:
                output.write(json.dumps({'path': url.path, 'authorization_present': 'Authorization' in self.headers}) + '\n')
        status = 200
        if url.path == '/api/tickets/search':
            text = params.get('text', [''])[0].casefold()
            body = {'tickets': [t for t in TICKETS if text in (t['subject'] + ' ' + t['message']).casefold()]}
        elif url.path == '/api/tickets/get':
            try:
                ticket_id = int(params['ticket_id'][0])
                body = {'ticket': next((t for t in TICKETS if t['id'] == ticket_id), None)}
            except (KeyError, ValueError):
                status, body = 400, {'error': 'Invalid ticket ID'}
        else:
            status, body = 404, {'error': 'Not found'}
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    server = LocalHTTPServer(('127.0.0.1', int(os.environ.get('SUPPORT_PORT', '4100'))), Handler)
    print(f'Support API listening on {server.server_address[1]}', flush=True)
    server.serve_forever()
