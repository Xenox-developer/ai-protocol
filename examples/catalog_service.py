import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


PRODUCTS = [
    {"id": 1, "name": "Black boots", "price": 150},
    {"id": 2, "name": "Brown boots", "price": 180},
    {"id": 3, "name": "White sneakers", "price": 120},
]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)

        if url.path == "/products/get":
            params = parse_qs(url.query)

            try:
                product_id = int(params["id"][0])
            except (KeyError, ValueError):
                self.send_error(400, "Expected integer id")
                return

            product = next(
                (item for item in PRODUCTS if item["id"] == product_id),
                None,
            )

            body = json.dumps({"product": product}).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if url.path != "/products/search":
            self.send_error(404)
            return

        params = parse_qs(url.query)
        query = params.get("query", [""])[0].lower()

        products = [
            product
            for product in PRODUCTS
            if query in product["name"].lower()
        ]

        body = json.dumps({"products": products}).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


print("Catalog service started on port 4000", flush=True)

server = ThreadingHTTPServer(("127.0.0.1", 4000), Handler)
server.serve_forever()