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
    from kherveref import mcp_dialog
    cfg = tmp_path / "c.json"
    host = mcp_hosts.Host.for_file(str(cfg))
    monkeypatch.setattr(mcp_dialog, "HOSTS", [host])
    monkeypatch.setattr(host, "is_running", lambda: False)
    monkeypatch.setattr(mcp_dialog.QMessageBox, "information", lambda *a: None)
    dlg = mcp_dialog.McpDialog()
    assert dlg._hosts.item(0).text().endswith("not connected")
    assert "Not connected" in dlg._status.text()
    dlg._connect_btn.click()
    assert dlg._hosts.item(0).text().endswith("— connected")
    assert dlg._disconnect_btn.isEnabled() and "Connected" in dlg._status.text()
    dlg._flavour.setCurrentIndex(1)
    assert dlg._snippet.toPlainText().startswith("claude mcp add kherveref")
    dlg.close()


def test_running_app_seen_when_pgrep_is_blind(monkeypatch):
    import subprocess
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")

    def run(args, **kw):
        out = ("/Applications/Claude.app/Contents/MacOS/Claude\n"
               if args[0] == "ps" else "")
        return subprocess.CompletedProcess(args, 1 if args[0] == "pgrep" else 0,
                                           out, "")
    monkeypatch.setattr(mcp_hosts.subprocess, "run", run)
    assert mcp_hosts.host_by_key("claude-desktop").is_running()
    assert not mcp_hosts.host_by_key("cursor").is_running()
