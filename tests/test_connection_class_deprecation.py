"""Ensure the Home Assistant connection-class deprecation stays resolved."""

import json
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "mypyllant"


def test_config_flow_uses_manifest_iot_class() -> None:
    config_flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))

    assert "CONN_CLASS_" not in config_flow
    assert "CONNECTION_CLASS" not in config_flow
    assert manifest["iot_class"] == "cloud_polling"
