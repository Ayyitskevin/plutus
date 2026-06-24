#!/usr/bin/env python3
"""Minimal local SMTP catcher for dogfood (accepts mail without AUTH)."""
from __future__ import annotations

import json
import os
import sys
import threading
from email import message_from_bytes
from socketserver import StreamRequestHandler, ThreadingTCPServer

LOG_PATH = os.environ.get("PLUTUS_SMTP_CATCHER_LOG")
_lock = threading.Lock()


class SMTPHandler(StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"220 plutus-smtp-catcher ready\r\n")
        mail_from: str | None = None
        rcpt_tos: list[str] = []
        while True:
            line = self.rfile.readline()
            if not line:
                break
            cmd = line.decode(errors="replace").strip()
            upper = cmd.upper()
            if upper.startswith("QUIT"):
                self.wfile.write(b"221 bye\r\n")
                break
            if upper.startswith("EHLO"):
                self.wfile.write(b"250-localhost\r\n")
                self.wfile.write(b"250 AUTH PLAIN LOGIN\r\n")
            elif upper.startswith("HELO"):
                self.wfile.write(b"250 localhost\r\n")
            elif upper.startswith("STARTTLS"):
                self.wfile.write(b"220 ready\r\n")
            elif upper.startswith("AUTH"):
                self.wfile.write(b"235 ok\r\n")
            elif upper.startswith("MAIL FROM:"):
                mail_from = cmd.split(":", 1)[1].strip()
                self.wfile.write(b"250 ok\r\n")
            elif upper.startswith("RCPT TO:"):
                rcpt_tos.append(cmd.split(":", 1)[1].strip())
                self.wfile.write(b"250 ok\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 send data\r\n")
                data: list[bytes] = []
                while True:
                    dl = self.rfile.readline()
                    if dl in (b".\r\n", b".\n"):
                        break
                    if dl.startswith(b"."):
                        dl = dl[1:]
                    data.append(dl)
                raw = b"".join(data)
                msg = message_from_bytes(raw)
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True) or b""
                            body = payload.decode(errors="replace")
                            break
                else:
                    payload = msg.get_payload(decode=True) or b""
                    if isinstance(payload, bytes):
                        body = payload.decode(errors="replace")
                    else:
                        body = str(payload)
                entry = {
                    "from": mail_from,
                    "to": list(rcpt_tos),
                    "subject": msg.get("Subject", ""),
                    "body": body,
                }
                with _lock:
                    if LOG_PATH:
                        with open(LOG_PATH, "a", encoding="utf-8") as fh:
                            fh.write(json.dumps(entry, sort_keys=True) + "\n")
                print(
                    f"[smtp] to={entry['to']} subject={entry['subject']!r}",
                    flush=True,
                )
                self.wfile.write(b"250 ok\r\n")
                mail_from = None
                rcpt_tos = []
            elif upper.startswith("RSET"):
                mail_from = None
                rcpt_tos = []
                self.wfile.write(b"250 ok\r\n")
            elif upper.startswith("NOOP"):
                self.wfile.write(b"250 ok\r\n")
            else:
                self.wfile.write(b"502 not implemented\r\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 2525
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    server = ThreadingTCPServer((host, port), SMTPHandler)
    print(f"listening on smtp://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()