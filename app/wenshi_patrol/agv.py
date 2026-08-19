"""AGV status and continuous-motion clients for the demo."""

from __future__ import annotations

import copy
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from .protocol import encode_frame, parse_frames


STATUS_REQUEST = 0x044D
STATUS_RESPONSE = 0x2B5D
MOTION_COMMAND = 0x07DA
STOP_COMMAND = 0x07D0


class AGVStatusClient:
    def __init__(
        self,
        ip: str,
        port: int = 19204,
        interval_ms: int = 200,
        response_timeout_s: float = 0.8,
        log: Callable[[str], None] | None = None,
    ):
        self.ip = ip
        self.port = int(port)
        self.interval_ms = int(interval_ms)
        self.response_timeout_s = max(float(response_timeout_s), 0.2)
        self.log = log or (lambda _message: None)
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._status_event = threading.Event()
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._invalid_responses = 0
        self._last_response: dict[str, Any] | None = None
        self._status: dict[str, Any] = {
            "station": "",
            "x": None,
            "y": None,
            "angle": None,
            "confidence": None,
            "vx": 0.0,
            "vy": 0.0,
            "w": 0.0,
            "blocked": False,
            "block_reason": None,
            "battery": None,
            "charging": False,
            "emergency": False,
            "brake": False,
            "is_stop": True,
            "fatals": [],
            "errors": [],
            "warnings": [],
            "last_update_monotonic": 0.0,
        }

    @property
    def connected(self) -> bool:
        return self._running.is_set() and self._socket is not None

    def connect(self, timeout: float = 3.0) -> bool:
        self.disconnect()
        try:
            with self._lock:
                self._status["last_update_monotonic"] = 0.0
            self._status_event.clear()
            self._buffer.clear()
            sock = socket.create_connection((self.ip, self.port), timeout=timeout)
            sock.settimeout(min(0.2, self.response_timeout_s))
            self._socket = sock
            self._running.set()
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="agv-status-poll",
                daemon=True,
            )
            self._thread.start()
            self.log(f"AGV 状态轮询已连接 {self.ip}:{self.port}")
            return True
        except OSError as exc:
            self.log(f"AGV 状态连接失败: {exc}")
            self.disconnect()
            return False

    def wait_for_status(self, timeout: float = 3.0, max_age: float | None = None) -> bool:
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            age = self.get_status()["status_age"]
            if age is not None and (max_age is None or age <= max_age):
                return True
            self._status_event.clear()
            self._status_event.wait(min(0.2, max(0.0, deadline - time.monotonic())))
        return False

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            result = copy.deepcopy(self._status)
            last_response = copy.deepcopy(self._last_response)
        updated = float(result.pop("last_update_monotonic", 0.0))
        result["status_age"] = time.monotonic() - updated if updated > 0 else None
        result["connected"] = self.connected
        result["last_response"] = last_response
        return result

    def disconnect(self):
        self._running.clear()
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _poll_loop(self):
        period = max(self.interval_ms / 1000.0, 0.05)
        next_poll = time.monotonic()
        while self._running.is_set() and self._socket is not None:
            try:
                self._socket.sendall(encode_frame(STATUS_REQUEST))
                response_deadline = time.monotonic() + self.response_timeout_s
                received_response = False
                while self._running.is_set() and time.monotonic() < response_deadline:
                    try:
                        data = self._socket.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        raise ConnectionError("AGV 19204 连接已被底盘关闭")
                    self._buffer.extend(data)
                    frames = parse_frames(self._buffer)
                    if not frames:
                        continue
                    received_response = True
                    for _command, value in frames:
                        self._consume_value(value)
                    break
                if not received_response:
                    self.log("AGV 19204 状态请求超时")
            except ConnectionError as exc:
                if self._running.is_set():
                    self.log(str(exc))
                break
            except OSError as exc:
                if self._running.is_set():
                    self.log(f"AGV 状态连接中断: {exc}")
                break
            except Exception as exc:
                self.log(f"AGV 状态解析异常: {exc}")
            next_poll = max(next_poll + period, time.monotonic())
            time.sleep(max(0.0, next_poll - time.monotonic()))
        self._running.clear()

    def _consume_value(self, value: Any):
        if isinstance(value, list):
            for item in value:
                self._consume_value(item)
            return
        if not isinstance(value, dict):
            return
        for key in ("data", "result"):
            nested = value.get(key)
            if isinstance(nested, dict):
                self._consume_value(nested)
                return
        self._update(value)

    @staticmethod
    def _float(value: Any, fallback: Any = None):
        if value is None or value == "":
            return fallback
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _boolean(value: Any, fallback: bool = False) -> bool:
        if value is None or value == "":
            return fallback
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _list(value: Any) -> list[Any]:
        if value is None or value == "":
            return []
        return value if isinstance(value, list) else [value]

    def _update(self, value: dict[str, Any]):
        with self._lock:
            self._last_response = copy.deepcopy(value)
        ret_code = value.get("ret_code")
        if ret_code not in (None, 0, "0"):
            self.log(f"AGV 19204 返回错误: ret_code={ret_code} {value.get('err_msg', '')}")
            return
        if not all(key in value for key in ("x", "y", "angle")):
            self._invalid_responses += 1
            if self._invalid_responses == 1 or self._invalid_responses % 10 == 0:
                self.log(f"AGV 19204 响应缺少 x/y/angle: {value}")
            return
        with self._lock:
            status = self._status
            station = value.get("current_station")
            if station:
                status["station"] = str(station)
            for key in ("x", "y", "angle", "confidence", "vx", "vy", "w"):
                status[key] = self._float(value.get(key), status.get(key))
            status["blocked"] = self._boolean(value.get("blocked"), status["blocked"])
            status["block_reason"] = value.get("block_reason", status["block_reason"])
            status["battery"] = value.get("battery_level", status["battery"])
            status["charging"] = self._boolean(value.get("charging"), status["charging"])
            status["emergency"] = self._boolean(value.get("emergency"), status["emergency"])
            status["brake"] = self._boolean(value.get("brake"), status["brake"])
            status["is_stop"] = self._boolean(value.get("is_stop"), status["is_stop"])
            status["fatals"] = self._list(value.get("fatals"))
            status["errors"] = self._list(value.get("errors"))
            status["warnings"] = self._list(value.get("warnings"))
            status["last_update_monotonic"] = time.monotonic()
        self._invalid_responses = 0
        self._status_event.set()


class AGVMotionClient:
    """Own port 19205 and refresh the current command until its watchdog expires."""

    def __init__(
        self,
        ip: str,
        port: int = 19205,
        send_rate_hz: float = 20.0,
        watchdog_s: float = 0.3,
        log: Callable[[str], None] | None = None,
    ):
        self.ip = ip
        self.port = int(port)
        self.period = 1.0 / max(float(send_rate_hz), 1.0)
        self.watchdog_s = max(float(watchdog_s), self.period * 2.0)
        self.log = log or (lambda _message: None)
        self._socket: socket.socket | None = None
        self._running = threading.Event()
        self._sender: threading.Thread | None = None
        self._receiver: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._desired = (0.0, 0.0)
        self._valid_until = 0.0
        self._active = False
        self._stop_sent = True
        self._response_buffer = bytearray()
        self.last_error = ""

    @property
    def connected(self) -> bool:
        return self._running.is_set() and self._socket is not None

    def connect(self, timeout: float = 3.0) -> bool:
        self.disconnect()
        try:
            with self._command_lock:
                self._desired = (0.0, 0.0)
                self._valid_until = 0.0
                self._active = False
                self._stop_sent = True
            self._response_buffer.clear()
            sock = socket.create_connection((self.ip, self.port), timeout=timeout)
            sock.settimeout(0.5)
            self._socket = sock
            self._running.set()
            self.last_error = ""
            self._sender = threading.Thread(target=self._send_loop, name="agv-motion-send", daemon=True)
            self._receiver = threading.Thread(
                target=self._receive_loop,
                name="agv-motion-receive",
                daemon=True,
            )
            self._sender.start()
            self._receiver.start()
            self.log(f"AGV 连续运动端口已连接 {self.ip}:{self.port}")
            return True
        except OSError as exc:
            self.last_error = str(exc)
            self.log(f"AGV 连续运动端口连接失败: {exc}")
            self.disconnect()
            return False

    def set_velocity(self, vx: float, w: float):
        with self._command_lock:
            self._desired = (float(vx), float(w))
            self._valid_until = time.monotonic() + self.watchdog_s
            self._active = True
            self._stop_sent = False

    def stop(self):
        with self._command_lock:
            self._desired = (0.0, 0.0)
            self._valid_until = 0.0
            self._active = False
        self._send_stop_once()

    def disconnect(self):
        if self._socket is not None:
            self.stop()
            time.sleep(min(self.period, 0.05))
        self._running.clear()
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        for thread in (self._sender, self._receiver):
            if thread and thread.is_alive():
                thread.join(timeout=1.0)
        self._sender = None
        self._receiver = None

    def _send_frame(self, command: int, payload: dict[str, Any] | None = None):
        sock = self._socket
        if sock is None:
            raise RuntimeError("AGV 连续运动端口未连接")
        with self._send_lock:
            sock.sendall(encode_frame(command, payload))

    def _send_stop_once(self, force: bool = False):
        if self._socket is None:
            return
        with self._command_lock:
            if self._stop_sent and not force:
                return
            self._stop_sent = True
        try:
            self._send_frame(STOP_COMMAND)
        except (OSError, RuntimeError) as exc:
            self.last_error = str(exc)

    def _send_loop(self):
        deadline = time.monotonic()
        while self._running.is_set():
            now = time.monotonic()
            with self._command_lock:
                vx, w = self._desired
                active = self._active and now <= self._valid_until
            try:
                if active:
                    self._send_frame(
                        MOTION_COMMAND,
                        {"vx": vx, "vy": 0.0, "w": w, "steer": 0.0},
                    )
                else:
                    self._send_stop_once()
            except (OSError, RuntimeError) as exc:
                self.last_error = str(exc)
                self.log(f"AGV 运动命令发送失败: {exc}")
                self._running.clear()
                break
            deadline += self.period
            time.sleep(max(0.0, deadline - time.monotonic()))

    def _receive_loop(self):
        while self._running.is_set() and self._socket is not None:
            try:
                data = self._socket.recv(65536)
                if not data:
                    if self._running.is_set():
                        self.last_error = "19205 连接已被底盘关闭"
                    break
                self._response_buffer.extend(data)
                for _command, value in parse_frames(self._response_buffer):
                    if isinstance(value, dict):
                        ret_code = value.get("ret_code", 0)
                        if ret_code not in (None, 0, "0"):
                            self.last_error = f"ret_code={ret_code} {value.get('err_msg', '')}"
            except socket.timeout:
                continue
            except OSError as exc:
                if self._running.is_set():
                    self.last_error = str(exc)
                break
        self._running.clear()
