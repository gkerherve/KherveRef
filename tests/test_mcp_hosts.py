import json

from kherveref import mcp_hosts


def test_connect_keeps_other_servers_and_backs_up(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}},
                               "theme": "dark"}))
    host = mcp_hosts.Host.for_file(str(cfg))
    assert host.status() == "not connected"
    out = host.connect()
    assert out["ok"] and out["action"] == "added"
    doc = json.loads(cfg.read_text())
    assert doc["theme"] == "dark" and "other" in doc["mcpServers"]
    assert doc["mcpServers"]["kherveref"]["args"][-1].endswith("mcp_server")
    assert host.status() == "connected"
    assert list(tmp_path.glob("*.bak"))
    assert host.disconnect()["action"] == "removed"
    assert set(json.loads(cfg.read_text())["mcpServers"]) == {"other"}


def test_refuses_to_overwrite_broken_json(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{ not json")
    out = mcp_hosts.Host.for_file(str(cfg)).connect()
    assert not out["ok"] and cfg.read_text() == "{ not json"


def test_dialog_connects(qapp, tmp_path, monkeypatch):
    from kherveref.mcp_dialog import McpDialog
    cfg = tmp_path / "c.json"
    host = mcp_hosts.Host.for_file(str(cfg))
    host.key = "claude-desktop"
    monkeypatch.setattr(mcp_hosts, "HOSTS", [host])
    monkeypatch.setattr("kherveref.mcp_dialog.QMessageBox.information",
                        lambda *a: None)
    dlg = McpDialog()
    [(_, status, button)] = dlg._rows
    assert status.text() == "not connected" and button.text() == "Connect"
    button.click()
    assert status.text() == "connected" and button.text() == "Disconnect"
    dlg.close()
