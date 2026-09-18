import threading
import time


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

