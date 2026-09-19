import threading
import time


def test_snapshot_tracks_joint_freshness_separately_from_tcp(monkeypatch):
    from wenshi_patrol.jaka import JakaClient

    now = iter((10.0, 11.0, 20.0, 21.0))
    monkeypatch.setattr("wenshi_patrol.jaka.time.monotonic", lambda: next(now))
    client = JakaClient("127.0.0.1")

    client._consume({"joint_pos": [1.0] * 6})
    first = client.snapshot()
    client._consume({"tcp_pos": [2.0] * 6})
    second = client.snapshot()

    assert first["joint_seq"] == 1
    assert first["joint_status_age"] == 1.0
    assert second["joint_seq"] == 1
    assert second["status_age"] == 1.0
    assert second["joint_status_age"] == 11.0


def test_joint_move_requires_three_distinct_joint_updates_for_arrival():
    from wenshi_patrol.jaka import JakaClient

    client = JakaClient("127.0.0.1", motion_start_wait_s=0.0)
    client._socket = object()
    client._running.set()
    client._consume({"joint_pos": [0.0] * 6})
    client.send = lambda _command, **_values: True

    arrived = client.joint_move([0.0] * 6, 1.0, 1.0, timeout=0.35)

    client._running.clear()
    client._socket = None
    assert arrived is False
    assert "超时" in client.last_error


def test_wait_for_joint_state_requires_an_update_after_wait_begins():
    from wenshi_patrol.jaka import JakaClient

    client = JakaClient("127.0.0.1")
    client._socket = object()
    client._running.set()
    client._consume({"joint_pos": [0.0] * 6})
    client.send = lambda _command, **_values: True

    fresh = client.wait_for_joint_state(timeout=0.15)

    client._running.clear()
    client._socket = None
    assert fresh is False


def test_connect_closes_socket_when_initial_refresh_send_fails(monkeypatch):
    from wenshi_patrol.jaka import JakaClient

    class Socket:
        def __init__(self):
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def shutdown(self, _how):
            pass

        def close(self):
            self.closed = True

    sock = Socket()
    client = JakaClient("127.0.0.1")
    monkeypatch.setattr("wenshi_patrol.jaka.socket.create_connection", lambda *_args, **_kwargs: sock)
    monkeypatch.setattr(client, "_receive_loop", lambda: None)

    def failed_send(_command, **_values):
        client.last_error = "initial refresh send failed"
        return False

    monkeypatch.setattr(client, "send", failed_send)

    assert client.connect() is False
    assert sock.closed is True
    assert client.connected is False
    assert client._socket is None
    assert client.last_error == "initial refresh send failed"


def test_joint_move_fails_when_joint_updates_go_stale_before_motion_timeout():
    from wenshi_patrol.jaka import JakaClient

    client = JakaClient(
        "127.0.0.1",
        motion_start_wait_s=0.0,
        joint_state_timeout_s=0.15,
    )
    client._socket = object()
    client._running.set()
    client.send = lambda _command, **_values: True

    started = time.monotonic()
    arrived = client.joint_move([1.0] * 6, 1.0, 1.0, timeout=3.0)
    elapsed = time.monotonic() - started

    client._running.clear()
    client._socket = None
    assert arrived is False
    assert elapsed < 1.0
    assert "关节状态" in client.last_error


def test_concurrent_stop_cannot_be_overtaken_by_an_already_started_joint_move():
    """A move that started first must send before stop_program, never after it."""
    from wenshi_patrol.jaka import JakaClient

    client = JakaClient("127.0.0.1", motion_start_wait_s=0.0)
    client._socket = object()
    client._running.set()
    commands = []
    clear_entered = threading.Event()
    release_clear = threading.Event()
    stop_started = threading.Event()
    real_cancel = client._cancel

    class BlockingCancel:
        def clear(self):
            clear_entered.set()
            assert release_clear.wait(timeout=1.0)
            real_cancel.clear()

        def set(self):
            real_cancel.set()

        def is_set(self):
            return real_cancel.is_set()

    client._cancel = BlockingCancel()
    client.send = lambda command, **_values: commands.append(command) or True

    move = threading.Thread(
        target=lambda: client.joint_move([0.0] * 6, 1.0, 1.0, timeout=0.01)
    )

    def request_stop():
        stop_started.set()
        client.stop()

    stop = threading.Thread(target=request_stop)
    move.start()
    assert clear_entered.wait(timeout=1.0)
    stop.start()
    assert stop_started.wait(timeout=1.0)
    time.sleep(0.02)
    release_clear.set()
    move.join(timeout=1.0)
    stop.join(timeout=1.0)
    client._running.clear()
    client._socket = None

    motion_commands = [name for name in commands if name in {"joint_move", "stop_program"}]
    assert motion_commands == ["joint_move", "stop_program"]


def test_joint_move_preserves_controller_error_when_stop_arrives_during_start_wait():
    from wenshi_patrol.jaka import JakaClient

    client = JakaClient("127.0.0.1", motion_start_wait_s=0.2)
    client._socket = object()
    client._running.set()
    move_sent = threading.Event()

    def send(command, **_values):
        if command == "joint_move":
            move_sent.set()
        return True

    client.send = send
    result = []
    move = threading.Thread(
        target=lambda: result.append(client.joint_move([0.0] * 6, 1.0, 1.0))
    )
    move.start()
    assert move_sent.wait(timeout=1.0)
    client._consume({"ret_code": -7, "err_msg": "servo fault"})
    client.stop()
    move.join(timeout=1.0)
    client._running.clear()
    client._socket = None

    assert result == [False]
    assert client.last_error == "ret_code=-7 servo fault"
