"""Read-only socket probes used by yubei.

This module intentionally has no motion command constants or write protocol.
"""

from __future__ import annotations

try:
    from .network_check import ProbeResult, probe_tcp
except ImportError:  # direct module execution
    from network_check import ProbeResult, probe_tcp


def read_only_probe(host: str, port: int, timeout_s: float) -> ProbeResult:
    return probe_tcp(host, port, timeout_s)
