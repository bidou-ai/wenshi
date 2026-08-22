from yubei.network_check import default_route, probe_tcp


def test_probe_tcp_reports_refused_without_raising():
    result = probe_tcp("127.0.0.1", 1, 0.01)
    assert result.ok is False
    assert result.error


def test_probe_tcp_reports_open_socket(monkeypatch):
    import socket

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("yubei.network_check.socket.create_connection", lambda *_args, **_kwargs: FakeSocket())
    result = probe_tcp("127.0.0.1", 1234, 0.5)
    assert result.ok is True


def test_default_route_returns_none_when_socket_creation_is_denied(monkeypatch):
    def denied(*_args, **_kwargs):
        raise OSError("operation not permitted")

    monkeypatch.setattr("yubei.network_check.socket.socket", denied)

    assert default_route() is None
