"""Safe, read-only JAKA teaching transport.

The transport sends only status queries. It never sends power, enable, stop,
joint_move, or any other motion command.
"""

from __future__ import annotations

import json
import socket
from typing import Any


class TeachingClient:
    def __init__(self, host: str, port: int = 10001, timeout_s: float = 3.0, socket_factory=None):
        self.host = host
        self.port = int(port)
        self.timeout_s = max(float(timeout_s), 0.1)
        self.socket_factory = socket_factory or socket.create_connection
        self.sock = None

    def connect(self) -> None:
        self.sock = self.socket_factory((self.host, self.port), timeout=self.timeout_s)
        self.sock.settimeout(self.timeout_s)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _query(self, command: str) -> dict[str, Any]:
        if command not in {"get_joint_pos", "get_tcp_pos"}:
            raise ValueError("teaching client only permits read-only status queries")
        if self.sock is None:
            raise RuntimeError("teaching client is not connected")
        self.sock.sendall(json.dumps({"cmdName": command}, separators=(",", ":")).encode("utf-8"))
        data = self.sock.recv(8192)
        if not data:
            raise ConnectionError("JAKA closed teaching connection")
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JAKA response is not an object")
        return value

    def read_joint(self) -> list[float]:
        value = self._query("get_joint_pos")
        for key in ("jointPosition", "joint", "data"):
            candidate = value.get(key)
            if isinstance(candidate, list) and len(candidate) == 6:
                return [float(item) for item in candidate]
        raise ValueError("JAKA response has no six-joint position")

    def read_tcp(self) -> list[float] | None:
        value = self._query("get_tcp_pos")
        for key in ("tcpPosition", "tcp", "data"):
            candidate = value.get(key)
            if isinstance(candidate, list) and len(candidate) >= 6:
                return [float(item) for item in candidate[:6]]
        return None

