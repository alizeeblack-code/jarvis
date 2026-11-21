"""Tests for network address detection, project dev_port setting, and project-bar UI."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
import store as store_mod  # noqa: E402
from store import ProjectStore  # noqa: E402


SAMPLE_IFCONFIG = """
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255
bridge0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 169.254.12.34 netmask 0xffff0000
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 100.101.102.103 --> 100.101.102.103 netmask 0xffffffff
utun10: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 9000
\tinet 198.18.0.1 --> 198.18.0.1 netmask 0xffff0000
"""


def test_parse_network_interfaces_filters_proxy_and_prefers_lan():
    res = server._parse_network_interfaces(SAMPLE_IFCONFIG)
    assert res["localhost"] == "localhost"
    assert res["lan_ip"] == "192.168.1.20"
    assert res["lan_ips"] == ["192.168.1.20"]
    assert res["tailscale_ip"] == "100.101.102.103"
    assert res["tailscale_ips"] == ["100.101.102.103"]


def test_parse_network_interfaces_prefers_primary_ip_when_valid():
    multi_lan = SAMPLE_IFCONFIG + "\nen1: flags=8863\n\tinet 10.0.0.42 netmask 0xffffff00\n"
    res = server._parse_network_interfaces(multi_lan, primary_ip="10.0.0.42")
    assert res["lan_ip"] == "10.0.0.42"
    assert res["lan_ips"] == ["10.0.0.42", "192.168.1.20"]

    # Fake-IP primary_ip from proxy TUN must be ignored
    res_proxy = server._parse_network_interfaces(SAMPLE_IFCONFIG, primary_ip="198.18.0.1")
    assert res_proxy["lan_ip"] == "192.168.1.20"


def test_api_network_addresses_returns_default_port():
    fake = {
        "localhost": "localhost",
        "lan_ip": "192.168.1.20",
        "lan_ips": ["192.168.1.20"],
        "tailscale_ip": "100.101.102.103",
        "tailscale_ips": ["100.101.102.103"],
        "tailscale_dns": "mac.tailnet.ts.net",
    }
    with patch.object(server, "_detect_network_addresses", return_value=fake):
        client = TestClient(server.app, base_url="http://localhost:5180", client=("127.0.0.1", 50000))
        resp = client.get("/api/network-addresses")
    assert resp.status_code == 200
    body = resp.json()
    assert body["localhost"] == "localhost"
    assert body["lan_ip"] == "192.168.1.20"
    assert body["tailscale_ip"] == "100.101.102.103"
    assert body["tailscale_dns"] == "mac.tailnet.ts.net"
    assert body["default_port"] == 5180


def test_project_store_assigns_independent_dev_ports(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    store = ProjectStore()

    p1_dir = tmp_path / "repo1"
    p2_dir = tmp_path / "repo2"
    p3_dir = tmp_path / "repo3"
    p1_dir.mkdir()
    p2_dir.mkdir()
    p3_dir.mkdir()

    p1 = store.create_project("demo1", str(p1_dir), inject=False)
    p2 = store.create_project("demo2", str(p2_dir), inject=False)
    p3 = store.create_project("demo3", str(p3_dir), inject=False)
    ports = [p1["settings"]["dev_port"], p2["settings"]["dev_port"], p3["settings"]["dev_port"]]
    assert all(isinstance(p, int) and 1 <= p <= 65535 for p in ports)
    assert len(set(ports)) == 3
    assert ports == [5200, 5201, 5202]

    # Updating to valid non-conflicting port succeeds
    updated = store.update_project(p1["id"], settings={"dev_port": "3000"})
    assert updated["settings"]["dev_port"] == 3000

    # Clearing port re-allocates a free independent port instead of None
    cleared = store.update_project(p1["id"], settings={"dev_port": None})
    assert cleared["settings"]["dev_port"] == 5200
    assert cleared["settings"]["dev_port"] not in {p2["settings"]["dev_port"], p3["settings"]["dev_port"]}

    # Conflicting port across projects is rejected
    with pytest.raises(ValueError, match="demo2"):
        store.update_project(p1["id"], settings={"dev_port": p2["settings"]["dev_port"]})

    # Invalid ports are rejected
    for bad in (0, 70000, "abc", True):
        with pytest.raises(ValueError):
            store.update_project(p1["id"], settings={"dev_port": bad})


def test_project_store_backfills_legacy_and_duplicate_ports(tmp_path, monkeypatch):
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps({
        "next_id": 4,
        "projects": [
            {"id": 1, "name": "a", "path": str(tmp_path / "a"), "created_at": "2026-01-01", "tasks": [], "settings": {}},
            {"id": 2, "name": "b", "path": str(tmp_path / "b"), "created_at": "2026-01-01", "tasks": [], "settings": {"dev_port": 3000}},
            {"id": 3, "name": "c", "path": str(tmp_path / "c"), "created_at": "2026-01-01", "tasks": [], "settings": {"dev_port": 3000}},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", projects_file)
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    store = ProjectStore()

    all_projects = store.get_all_projects()
    by_name = {p["name"]: p["settings"]["dev_port"] for p in all_projects}
    assert by_name["b"] == 3000
    assert len(set(by_name.values())) == 3
    assert all(isinstance(v, int) and v not in {5100, 5180, 7890} for v in by_name.values())


def test_api_put_project_rejects_port_conflicts(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    tmp_store = ProjectStore()
    p1 = tmp_store.create_project("alpha", str(tmp_path / "alpha"), inject=False)
    p2 = tmp_store.create_project("beta", str(tmp_path / "beta"), inject=False)
    monkeypatch.setattr(server, "store", tmp_store)

    client = TestClient(server.app, base_url="http://localhost:5180", client=("127.0.0.1", 50000))
    # Conflict with another project
    r_dup = client.put(f"/api/projects/{p2['id']}", json={"settings": {"dev_port": p1["settings"]["dev_port"]}})
    assert r_dup.status_code == 200
    assert "alpha" in r_dup.json()["error"]

    # Conflict with Jarvis running port
    r_self = client.put(f"/api/projects/{p2['id']}", json={"settings": {"dev_port": 5180}})
    assert r_self.status_code == 200
    assert "Jarvis" in r_self.json()["error"]

    # Valid distinct port succeeds
    r_ok = client.put(f"/api/projects/{p2['id']}", json={"settings": {"dev_port": 4100}})
    assert r_ok.status_code == 200
    assert r_ok.json()["project"]["settings"]["dev_port"] == 4100


def test_project_bar_buttons_html():
    html = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text("utf-8")
    assert 'id="projectUrlsBtn"' in html
    assert 'id="projectUrlsPopover"' in html
    assert 'id="projectDevPortAuto"' in html
    assert 'id="projectUrlsError"' in html
    assert 'id="projectSettingsBtn"' in html
    assert 'id="settingDevPort"' in html
    assert 'id="projectModalError"' in html
    assert 'id="claudeMdBtn"' not in html
    assert 'id="progressMdBtn"' not in html
