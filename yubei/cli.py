"""Small shared argparse helpers for yubei command line tools."""

from __future__ import annotations

import argparse


def add_camera_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default="http://192.168.192.203:18080")
    parser.add_argument("--timeout", type=float, default=2.0)

