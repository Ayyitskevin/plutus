#!/usr/bin/env python3
"""Minimal local webhook receiver for order-paid dogfood (PLUTUS_ORDER_WEBHOOK_URL)."""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body.decode() or "{}")
        except json.JSONDecodeError:
            payload = {"raw": body.decode(errors="replace")[:500]}
        print(f"[notify] {self.path} {json.dumps(payload, sort_keys=True)}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_args) -> None:
        return


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    server = HTTPServer((host, port), Handler)
    print(f"listening on http://{host}:{port}/plutus-events", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()