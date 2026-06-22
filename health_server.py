#!/usr/bin/env python3
"""Minimal HTTP health check server for Railway."""
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.getenv("PORT", 8080))

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    print(f"Health server on port {PORT}")
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()
