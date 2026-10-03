"""Skalowanie okna na Linuksie: Xft.dpi z GNOME -> skala Tk."""
import autoshutdown as app


def test_xft_200_procent():
    assert app.parse_xft_scale("Xft.antialias:\t1\nXft.dpi:\t192\nXft.hinting:\t1\n") == 2.0


def test_xft_150_procent():
    assert app.parse_xft_scale("Xft.dpi: 144") == 1.5


def test_brak_lub_smieci_to_bez_zmian():
    assert app.parse_xft_scale("") == 1.0
    assert app.parse_xft_scale("Xft.dpi:\tabc") == 1.0
    assert app.parse_xft_scale("Xft.dpi:\t0") == 1.0
    assert app.parse_xft_scale("Xcursor.size: 48") == 1.0


def test_poza_linuksem_bez_xrdb(monkeypatch):
    monkeypatch.setattr(app.sys, "platform", "win32")
    assert app.screen_scale_info()[0] == 1.0


def test_linux_czyta_xrdb(monkeypatch):
    class R:
        stdout = "Xft.dpi:\t192\n"
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: R())
    assert app.screen_scale_info()[0] == 2.0


def test_brak_xrdb_nie_wywraca(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("xrdb")
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setattr(app.subprocess, "run", boom)
    monkeypatch.setattr(app.Path, "home", lambda: tmp_path)  # bez monitors.xml
    assert app.screen_scale_info(wait_s=0)[0] == 1.0


def test_skala_z_monitors_xml():
    xml = "<monitors><configuration><logicalmonitor><scale>2</scale></logicalmonitor>" \
          "<logicalmonitor><scale>1.25</scale></logicalmonitor></configuration></monitors>"
    assert app.parse_monitors_scale(xml) == 2.0
    assert app.parse_monitors_scale("<monitors/>") == 1.0


def test_autostart_przed_xft_bierze_monitors_xml(monkeypatch, tmp_path):
    class R:
        stdout = "Xft.antialias:\t1\n"  # gsd-xsettings jeszcze nie ustawil Xft.dpi
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(app.Path, "home", lambda: tmp_path)
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config/monitors.xml").write_text("<scale>2</scale>")
    assert app.screen_scale_info(wait_s=0)[0] == 2.0


def test_bez_waylanda_nie_czeka(monkeypatch, tmp_path):
    """CI (Xvfb) nie ma Xft.dpi - start GUI nie moze czekac 8 s (test GUI end-to-end by padl)."""
    import time

    class R:
        stdout = ""
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(app.Path, "home", lambda: tmp_path)
    t0 = time.monotonic()
    assert app.screen_scale_info()[0] == 1.0
    assert time.monotonic() - t0 < 1.0


class _Clock:
    """Falszywy zegar: sleep przesuwa czas, test nie czeka naprawde."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _gnome_wayland(monkeypatch, tmp_path):
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    monkeypatch.setattr(app.Path, "home", lambda: tmp_path)


def test_autostart_czeka_az_gnome_poda_xft(monkeypatch, tmp_path):
    _gnome_wayland(monkeypatch, tmp_path)
    answers = iter(["", "", "Xft.antialias:\t1\n", "Xft.dpi:\t192\n"])
    monkeypatch.setattr(app, "_xrdb_query", lambda: next(answers))
    clock = _Clock()
    assert app.screen_scale_info(sleep=clock.sleep, now=clock.now) == (2.0, "Xft.dpi")
    assert clock.t == 3 * app.XFT_POLL_S  # trzy pauzy, potem trafienie - nie czeka do konca limitu


def test_gnome_bez_xft_po_limicie_bierze_monitors(monkeypatch, tmp_path):
    _gnome_wayland(monkeypatch, tmp_path)
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config/monitors.xml").write_text(
        "<logicalmonitor><scale>2</scale><primary>yes</primary></logicalmonitor>")
    monkeypatch.setattr(app, "_xrdb_query", lambda: "")
    clock = _Clock()
    assert app.screen_scale_info(sleep=clock.sleep, now=clock.now) == (2.0, "monitors.xml")
    assert app.XFT_WAIT_S <= clock.t <= app.XFT_WAIT_S + app.XFT_POLL_S


def test_brak_xrdb_nie_czeka_nawet_w_gnome(monkeypatch, tmp_path):
    _gnome_wayland(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_xrdb_query", lambda: None)
    clock = _Clock()
    assert app.screen_scale_info(sleep=clock.sleep, now=clock.now) == (1.0, "domyslna")
    assert clock.t == 0


def test_kde_wayland_nie_czeka(monkeypatch, tmp_path):
    _gnome_wayland(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(app, "_xrdb_query", lambda: "")
    clock = _Clock()
    app.screen_scale_info(sleep=clock.sleep, now=clock.now)
    assert clock.t == 0


def _cfg(connectors: list[str], scale: float) -> str:
    specs = "".join(f"<monitor><monitorspec><connector>{c}</connector></monitorspec></monitor>"
                    for c in connectors)
    return (f"<configuration><logicalmonitor><scale>{scale}</scale><primary>yes</primary>{specs}"
            "</logicalmonitor></configuration>")


def test_monitors_bierze_uklad_podlaczonych_ekranow():
    # dwa uklady, kazdy z wlasnym monitorem glownym: sam laptop 1x, laptop + dok 2x
    xml = "<monitors>" + _cfg(["eDP-1"], 1) + _cfg(["eDP-1", "HDMI-A-1"], 2) + "</monitors>"
    assert app.parse_monitors_scale(xml, {"eDP-1"}) == 1.0
    assert app.parse_monitors_scale(xml, {"eDP-1", "HDMI-A-1"}) == 2.0
    assert app.parse_monitors_scale(xml, {"DP-3"}) == 2.0  # brak dopasowania -> najwieksza


def test_zlacza_z_sysfs(tmp_path):
    for name, status in (("card1-eDP-1", "connected"), ("card1-HDMI-A-1", "disconnected"),
                         ("card1-Writeback-1", "unknown")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "status").write_text(status + "\n")
    assert app.connected_connectors(tmp_path) == {"eDP-1"}


def test_zepsuty_monitors_xml_nie_wywraca_startu(monkeypatch, tmp_path):
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(app, "_xrdb_query", lambda: "")
    monkeypatch.setattr(app.Path, "home", lambda: tmp_path)
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config/monitors.xml").write_bytes(b"<scale>2</scale>\xff\xfe")
    assert app.screen_scale_info() == (2.0, "monitors.xml")


def test_timeout_xrdb_to_nie_brak_programu(monkeypatch):
    def slow(*a, **k):
        raise app.subprocess.TimeoutExpired("xrdb", 2)
    monkeypatch.setattr(app.subprocess, "run", slow)
    assert app._xrdb_query() == ""      # przejsciowe - petla czeka dalej

    def missing(*a, **k):
        raise FileNotFoundError("xrdb")
    monkeypatch.setattr(app.subprocess, "run", missing)
    assert app._xrdb_query() is None    # trwale - bez czekania
