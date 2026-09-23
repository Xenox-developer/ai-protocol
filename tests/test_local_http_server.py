"""Local demos must serve requests even when reverse DNS is unavailable."""

import http.client
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from catalog_service import Handler as CatalogHandler
from local_http_server import LocalHTTPServer
from support_service import Handler as SupportHandler


class LocalServerTests(unittest.TestCase):
    def test_both_services_start_and_respond_without_reverse_dns(self):
        for handler, path, expected in (
            (CatalogHandler, "/products/get?id=1", b"Black boots"),
            (SupportHandler, "/api/tickets/get?ticket_id=101", b'"id": 101'),
        ):
            with self.subTest(handler=handler.__module__), patch(
                "socket.getfqdn", side_effect=AssertionError("Unexpected reverse DNS")
            ), LocalHTTPServer(("127.0.0.1", 0), handler) as server:
                self.assertEqual(server.server_name, "127.0.0.1")
                self.assertEqual(server.server_port, server.server_address[1])
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                connection = http.client.HTTPConnection(*server.server_address, timeout=2)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIn(expected, response.read())
                finally:
                    connection.close()
                    server.shutdown()
                    worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
