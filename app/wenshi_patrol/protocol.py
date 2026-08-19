"""SEER/Bilinx NetProtocol frame encoding and stream parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


FRAME_HEAD = bytes.fromhex("5A010001")
FRAME_HEADER_LEN = 16
MAX_PAYLOAD_LEN = 8 * 1024 * 1024


def encode_frame(command: int, payload: Mapping[str, Any] | None = None) -> bytes:
    data = b""
    if payload is not None:
        data = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    if not 0 <= int(command) <= 0xFFFF:
        raise ValueError(f"command 超出 16 位范围: {command}")
    return b"".join(
        (
            FRAME_HEAD,
            len(data).to_bytes(4, byteorder="big"),
            int(command).to_bytes(2, byteorder="big"),
            bytes(6),
            data,
        )
    )


def extract_json_objects(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    objects: list[Any] = []
    index = 0
    while index < len(text):
        starts = [value for value in (text.find("{", index), text.find("[", index)) if value >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        objects.append(value)
        index = start + consumed
    return objects


def decode_payload(payload: bytes) -> list[Any]:
    text = payload.decode("utf-8", errors="ignore").strip()
    if not text:
        return []
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        return extract_json_objects(text)


def parse_frames(buffer: bytearray) -> list[tuple[int, Any]]:
    """Remove and return all complete frames currently in *buffer*."""
    frames: list[tuple[int, Any]] = []
    while True:
        head_index = buffer.find(FRAME_HEAD)
        if head_index < 0:
            if len(buffer) > len(FRAME_HEAD):
                del buffer[: -len(FRAME_HEAD)]
            break
        if head_index:
            del buffer[:head_index]
        if len(buffer) < FRAME_HEADER_LEN:
            break

        payload_len = int.from_bytes(buffer[4:8], byteorder="big")
        if payload_len > MAX_PAYLOAD_LEN:
            del buffer[:4]
            continue
        frame_len = FRAME_HEADER_LEN + payload_len
        if len(buffer) < frame_len:
            break

        command = int.from_bytes(buffer[8:10], byteorder="big")
        payload = bytes(buffer[FRAME_HEADER_LEN:frame_len])
        del buffer[:frame_len]
        values = decode_payload(payload)
        if not values:
            frames.append((command, None))
        else:
            frames.extend((command, value) for value in values)
    return frames
