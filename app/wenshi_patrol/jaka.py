"""Minimal JAKA TCP client that never changes power or enable state."""

from __future__ import annotations

import copy
import json
import socket
import threading
import time
from collections.abc import Callable
from typing import Any


class JakaClient:
    def __init__(
        self,
        ip: str,
        port: int = 10001,
        joint_tolerance_deg: float = 0.5,
        command_interval_s: float = 0.1,
        motion_start_wait_s: float = 0.5,
        motion_stall_timeout_s: float = 10.0,
        motion_progress_epsilon_deg: float = 0.05,
        motion_progress_log_s: float = 5.0,
        log: Callable[[str], None] | None = None,
    ):
        self.ip = ip
        self.port = int(port)
        self.joint_tolerance_deg = float(joint_tolerance_deg)
        self.command_interval_s = max(float(command_interval_s), 0.0)
        self.motion_start_wait_s = max(float(motion_start_wait_s), 0.0)
        self.motion_stall_timeout_s = max(float(motion_stall_timeout_s), 1.0)
        self.motion_progress_epsilon_deg = max(float(motion_progress_epsilon_deg), 0.001)
        self.motion_progress_log_s = max(float(motion_progress_log_s), 0.0)
        self.log = log or (lambda _message: None)
        self._socket: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._data_lock = threading.Lock()
        self._running = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._joint: list[float] | None = None
        self._tcp: list[float] | None = None
        self._last_update = 0.0
        self._last_send = 0.0
        self._last_control_response: dict[str, Any] | None = None
        self.last_error = ""

    @property
    def connected(self) -> bool:
        return self._running.is_set() and self._socket is not None

    def connect(self, timeout: float = 3.0) -> bool:
        """Connect only. This deliberately sends no power or enable command."""
        if self.connected:
            return True
        self.disconnect()
        try:
            sock = socket.create_connection((self.ip, self.port), timeout=timeout)
            sock.settimeout(0.5)
            self._socket = sock
            self._running.set()
            self._cancel.clear()
            self._last_send = 0.0
            self.last_error = ""
            self._thread = threading.Thread(target=self._receive_loop, name="jaka-receive", daemon=True)
            self._thread.start()
            self.refresh()
            self.log(f"JAKA 已连接 {self.ip}:{self.port}（未执行上电或使能）")
            return True
        except OSError as exc:
            self.last_error = str(exc)
            self.log(f"JAKA 连接失败: {exc}")
            self.disconnect()
            return False

    def disconnect(self):
        """Close the socket without disable_robot or power_off."""
        self._running.clear()
        self._cancel.set()
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
        with self._data_lock:
            self._joint = None
            self._tcp = None
            self._last_update = 0.0

    def send(self, command: str, **values: Any) -> bool:
        sock = self._socket
        if sock is None:
            self.last_error = "JAKA 未连接"
            return False
        payload = {"cmdName": command, **values}
        try:
            with self._send_lock:
                remaining = self.command_interval_s - (time.monotonic() - self._last_send)
                if remaining > 0:
                    time.sleep(remaining)
                sock.sendall(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
                self._last_send = time.monotonic()
            return True
        except OSError as exc:
            self.last_error = str(exc)
            self._running.clear()
            return False

    def refresh(self):
        self.send("get_joint_pos")
        self.send("get_tcp_pos")

    def snapshot(self) -> dict[str, Any]:
        with self._data_lock:
            joint = list(self._joint) if self._joint else None
            tcp = list(self._tcp) if self._tcp else None
            updated = self._last_update
            response = copy.deepcopy(self._last_control_response)
        return {
            "connected": self.connected,
            "joint": joint,
            "tcp": tcp,
            "status_age": time.monotonic() - updated if updated else None,
            "last_control_response": response,
            "error": self.last_error,
        }

    def wait_for_joint_state(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.send("get_joint_pos")
            time.sleep(0.1)
            snapshot = self.snapshot()
            age = snapshot.get("status_age")
            if snapshot["joint"] is not None and age is not None and age <= 0.5:
                return True
        return False

    def joint_move(
        self,
        target: list[float],
        speed: float,
        accel: float,
        timeout: float = 120.0,
        tolerance_deg: float | None = None,
    ) -> bool:
        if len(target) != 6:
            self.last_error = "joint_move 需要 6 个关节角"
            return False
        self.last_error = ""
        self._cancel.clear()
        if not self.send(
            "joint_move",
            relFlag=0,
            jointPosition=[float(value) for value in target],
            speed=float(speed),
            accel=float(accel),
        ):
            return False

        time.sleep(self.motion_start_wait_s)
        arrival_tolerance = (
            self.joint_tolerance_deg
            if tolerance_deg is None
            else max(float(tolerance_deg), self.joint_tolerance_deg)
        )
        deadline = time.monotonic() + float(timeout)
        stable = 0
        last_joint: list[float] | None = None
        last_errors: list[float] | None = None
        best_error: float | None = None
        last_progress = time.monotonic()
        next_progress_log = last_progress + self.motion_progress_log_s
        while self.connected and not self._cancel.is_set() and time.monotonic() < deadline:
            self.send("get_joint_pos")
            time.sleep(0.1)
            if self.last_error:
                return False
            joint = self.snapshot()["joint"]
            if joint is None or len(joint) != 6:
                continue
            last_joint = joint
            last_errors = [abs(float(joint[i]) - float(target[i])) for i in range(6)]
            max_error = max(last_errors)
            now = time.monotonic()
            if best_error is None or max_error < best_error - self.motion_progress_epsilon_deg:
                best_error = max_error
                last_progress = now
            elif max_error > arrival_tolerance and now - last_progress >= self.motion_stall_timeout_s:
                index = max(range(6), key=last_errors.__getitem__)
                self.last_error = (
                    f"机械臂运动无进展: {now - last_progress:.1f}s内误差未改善，"
                    f"J{index + 1}误差={last_errors[index]:.3f}deg "
                    f"目标={float(target[index]):.3f} 实际={float(joint[index]):.3f}"
                )
                return False
            if self.motion_progress_log_s > 0 and now >= next_progress_log:
                index = max(range(6), key=last_errors.__getitem__)
                self.log(
                    f"机械臂运动中: J{index + 1}误差={last_errors[index]:.3f}deg "
                    f"目标={float(target[index]):.3f} 实际={float(joint[index]):.3f}"
                )
                next_progress_log = now + self.motion_progress_log_s
            if max_error <= arrival_tolerance:
                stable += 1
                if stable >= 3:
                    return True
            else:
                stable = 0
        if self._cancel.is_set():
            self.last_error = "机械臂运动已停止"
        elif not self.connected:
            self.last_error = "机械臂连接中断"
        elif last_joint is not None and last_errors is not None:
            index = max(range(6), key=last_errors.__getitem__)
            response = self.snapshot().get("last_control_response")
            self.last_error = (
                f"机械臂运动到位超时: J{index + 1}误差={last_errors[index]:.3f}deg "
                f"目标={float(target[index]):.3f} 实际={float(last_joint[index]):.3f} "
                f"容差={arrival_tolerance:.3f}deg "
                f"最后控制响应={response}"
            )
        else:
            self.last_error = "机械臂运动到位超时: 未收到有效关节角"
        return False

    def stop(self):
        self._cancel.set()
        if self.connected:
            self.send("stop_program")

    def _receive_loop(self):
        decoder = json.JSONDecoder()
        buffer = ""
        while self._running.is_set() and self._socket is not None:
            try:
                data = self._socket.recv(8192)
                if not data:
                    if self._running.is_set():
                        self.last_error = "JAKA 连接已被控制器关闭"
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while buffer:
                    start = buffer.find("{")
                    if start < 0:
                        buffer = ""
                        break
                    if start:
                        buffer = buffer[start:]
                    try:
                        value, consumed = decoder.raw_decode(buffer)
                    except json.JSONDecodeError:
                        break
                    buffer = buffer[consumed:].lstrip()
                    if isinstance(value, dict):
                        self._consume(value)
            except socket.timeout:
                continue
            except OSError as exc:
                if self._running.is_set():
                    self.last_error = str(exc)
                break
        self._running.clear()

    def _consume(self, value: dict[str, Any]):
        now = time.monotonic()
        has_position = False
        with self._data_lock:
            joint = value.get("joint_pos")
            if isinstance(joint, list) and len(joint) >= 6:
                self._joint = [float(item) for item in joint[:6]]
                self._last_update = now
                has_position = True
            tcp = value.get("tcp_pos")
            if isinstance(tcp, list) and len(tcp) >= 6:
                self._tcp = [float(item) for item in tcp[:6]]
                self._last_update = now
                has_position = True
            if not has_position:
                self._last_control_response = copy.deepcopy(value)
        if not has_position:
            self.log(f"JAKA 控制响应: {value}")
        for key in ("ret_code", "errorCode", "error_code", "err_code"):
            code = value.get(key)
            if code not in (None, 0, "0"):
                detail = value.get("err_msg") or value.get("errorMsg") or value.get("message") or ""
                self.last_error = f"{key}={code} {detail}".strip()
                break
