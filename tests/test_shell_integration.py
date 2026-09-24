from blueglance.config import Config
from blueglance.services import shell as shell_mod
from blueglance.session import SessionInfo


class FakeApp:
    def __init__(self):
        self.config = Config(persist=False)


def make(monkeypatch, desktop="ubuntu:gnome", on_disk=False, bundled=True):
    monkeypatch.setattr(shell_mod, "session_info",
                        lambda: SessionInfo(desktop=desktop, wayland=True, x11=False, flatpak=False))
    monkeypatch.setattr(shell_mod, "installed_on_disk", lambda: on_disk)
    monkeypatch.setattr(shell_mod, "bundled_extension_dir", lambda: "/src/ext" if bundled else None)
    return shell_mod.ShellIntegration(FakeApp())


def test_non_gnome_allows_fallback_unless_extension_runs(monkeypatch):
    integration = make(monkeypatch, desktop="kde")
    assert integration.settled and integration.fallback_allowed
    integration._set_active(True)  # e.g. GNOME Shell without XDG_CURRENT_DESKTOP set
    assert not integration.fallback_allowed


def test_gnome_waits_until_settled(monkeypatch):
    integration = make(monkeypatch)
    assert not integration.settled
    assert not integration.fallback_allowed
    integration._settle()
    # Extension not installed at all -> floating widget is the best we can do.
    assert integration.describe()[0] == "missing"
    assert integration.fallback_allowed


def test_gnome_installed_extension_never_shows_floating_widget(monkeypatch):
    integration = make(monkeypatch, on_disk=True)
    integration._settle()
    assert integration.describe() == ("pending", "Log out and back in once to finish setting up the widget", None)
    assert not integration.fallback_allowed

    integration.info = {"state": 2.0}  # known to the shell but switched off
    assert integration.describe()[0] == "disabled"
    assert not integration.fallback_allowed

    integration.info = {"state": 3.0, "error": "boom"}
    assert integration.describe()[0] == "error"
    assert integration.fallback_allowed


def test_active_extension_settles_immediately(monkeypatch):
    integration = make(monkeypatch)
    events = []
    integration.connect("changed", lambda *_: events.append(True))
    integration._set_active(True)
    assert integration.active and integration.settled
    assert not integration.fallback_allowed
    assert events


def test_auto_enable_happens_once(monkeypatch):
    integration = make(monkeypatch)
    calls = []
    monkeypatch.setattr(integration, "enable", lambda: calls.append("enable"))
    integration._set_info({"state": 6.0})
    integration._set_info({"state": 2.0})
    assert calls == ["enable"]
    assert integration.app.config["shell_extension_autoenabled"]
