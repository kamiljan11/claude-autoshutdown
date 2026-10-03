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
    assert app.linux_screen_scale() == 1.0


def test_linux_czyta_xrdb(monkeypatch):
    class R:
        stdout = "Xft.dpi:\t192\n"
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: R())
    assert app.linux_screen_scale() == 2.0


def test_brak_xrdb_nie_wywraca(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("xrdb")
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setattr(app.subprocess, "run", boom)
    assert app.linux_screen_scale() == 1.0
