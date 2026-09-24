from blueglance.services import autostart


def setup_paths(monkeypatch, tmp_path, with_system):
    user = tmp_path / "user" / "autostart" / autostart.FILE_NAME
    system = tmp_path / "etc" / "xdg" / "autostart" / autostart.FILE_NAME
    if with_system:
        system.parent.mkdir(parents=True)
        system.write_text(autostart.render_entry(["/usr/bin/blueglance"]))
    monkeypatch.setattr(autostart, "user_entry", lambda: user)
    monkeypatch.setattr(autostart, "system_entry", lambda: system if system.exists() else None)
    monkeypatch.setattr(autostart, "launch_command", lambda: ["/usr/bin/blueglance"])
    return user, system


def test_user_entry_only(monkeypatch, tmp_path):
    user, _system = setup_paths(monkeypatch, tmp_path, with_system=False)
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    assert user.exists() and autostart.is_enabled()
    assert "Exec=/usr/bin/blueglance --background" in user.read_text()
    autostart.set_enabled(False)
    assert not user.exists() and not autostart.is_enabled()


def test_system_entry_is_overridden_per_user(monkeypatch, tmp_path):
    user, _system = setup_paths(monkeypatch, tmp_path, with_system=True)
    assert autostart.is_enabled()  # enabled out of the box
    autostart.set_enabled(False)
    assert user.exists() and "Hidden=true" in user.read_text()
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    assert not user.exists() and autostart.is_enabled()


def test_exec_quoting():
    assert autostart.desktop_exec(["/opt/my apps/blueglance", "--background"]) == \
        '"/opt/my apps/blueglance" --background'
    assert autostart.desktop_exec(["env", "PYTHONPATH=/src", "/usr/bin/python3"]) == \
        "env PYTHONPATH=/src /usr/bin/python3"
