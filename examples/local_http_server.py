"""HTTP server for numeric loopback addresses, without startup DNS lookups."""

from http.server import ThreadingHTTPServer
from socketserver import TCPServer


class LocalHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer normally resolves a display name before it starts listening.
        # Local demos need only the bound address, even when DNS is unavailable.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
