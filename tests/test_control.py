from trading_bot import control


def test_status_reports_stopped_without_pid(monkeypatch, capsys):
    monkeypatch.setattr(control, "_pid", lambda: None)

    assert control.status() is False
    assert "STOPPED" in capsys.readouterr().out


def test_stop_is_idempotent(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(control, "PID_PATH", tmp_path / "bot.pid")
    monkeypatch.setattr(control, "STOP_PATH", tmp_path / "bot.stop")
    monkeypatch.setattr(control, "EXPECTED_PATH", tmp_path / "bot.expected")
    monkeypatch.setattr(control, "HEARTBEAT_PATH", tmp_path / "bot.heartbeat")
    monkeypatch.setattr(control, "_pid", lambda: None)

    assert control.stop() == 0
    assert "already stopped" in capsys.readouterr().out


def test_live_arm_is_short_lived_and_one_time(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "LIVE_ARM_PATH", tmp_path / "live.arm")

    assert control.arm_live() == 0
    assert control.consume_live_arm() is True
    assert control.consume_live_arm() is False


def test_tunnel_status_detects_orphan_via_local_api(monkeypatch, capsys):
    monkeypatch.setattr(control, "_read_pid", lambda path: None)
    monkeypatch.setattr(
        control,
        "_ngrok_api_tunnels",
        lambda: [("http://127.0.0.1:4040", "command_line", "https://example.test")],
    )

    assert control.tunnel_status() is True
    assert "ngrok API" in capsys.readouterr().out
