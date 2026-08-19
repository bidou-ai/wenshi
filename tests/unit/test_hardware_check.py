from pathlib import Path

from wenshi_patrol.config import load_config
from wenshi_patrol.hardware_check import configured_endpoints


ROOT = Path(__file__).resolve().parents[2]


def test_hardware_check_reads_wenshi_endpoints_from_config():
    endpoints = configured_endpoints(load_config(ROOT / "config" / "wenshi.yaml"))
    assert endpoints["agv_status"] == ("192.168.192.5", 19204)
    assert endpoints["agv_motion"] == ("192.168.192.5", 19205)
    assert endpoints["jaka"] == ("192.168.192.160", 10001)
    assert endpoints["camera_health"] == "http://192.168.192.203:18080/health"

