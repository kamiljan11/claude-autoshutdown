"""Testy warstwy aplikacji: blokada drugiej instancji, rotacja logu, konfiguracja.

Nie uruchamiaja GUI - importuja modul z podmienionym katalogiem stanu.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="tylko Windows")


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    """Modul autoshutdown z katalogiem stanu w tmp_path."""
    monkeypatch.setenv("CLAUDE_AUTOSHUTDOWN_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_AUTOSHUTDOWN_AUTOARM", raising=False)
    sys.modules.pop("autoshutdown", None)
    module = importlib.import_module("autoshutdown")
    yield module
    sys.modules.pop("autoshutdown", None)


# --------------------------------------------------------------------------- #
# katalog stanu
# --------------------------------------------------------------------------- #
def test_katalog_stanu_z_env(app, tmp_path: Path):
    assert app.APP_DIR == tmp_path
    assert app.CONFIG_PATH.parent == tmp_path
    assert app.LOG_PATH.parent == tmp_path


# --------------------------------------------------------------------------- #
# blokada drugiej instancji
# --------------------------------------------------------------------------- #
def test_brak_blokady_gdy_plik_nie_istnieje(app):
    assert app.another_instance_running() is None


@WINDOWS_ONLY
def test_wlasny_proces_jest_wykrywany_jako_zywa_instancja(app):
    app.claim_instance_lock()
    assert app.another_instance_running() == os.getpid()


def test_smiec_po_awarii_nie_blokuje(app):
    """Plik blokady po procesie, ktorego juz nie ma, musi byc zignorowany."""
    app.LOCK_FILE.write_text("999999999 123456789", encoding="utf-8")
    assert app.another_instance_running() is None


@WINDOWS_ONLY
def test_recykling_pid_nie_blokuje(app):
    """Ten sam PID, inny czas startu = inny proces, nie nasza instancja."""
    app.LOCK_FILE.write_text(f"{os.getpid()} 1", encoding="utf-8")
    assert app.another_instance_running() is None


@pytest.mark.parametrize("content", ["", "smieci", "123", "abc def", "\x00\x01"])
def test_uszkodzony_plik_blokady_nie_wywala_programu(app, content):
    app.LOCK_FILE.write_text(content, encoding="utf-8")
    assert app.another_instance_running() is None


# --------------------------------------------------------------------------- #
# rotacja logu
# --------------------------------------------------------------------------- #
def test_maly_log_nie_jest_rotowany(app):
    app.log_line("pierwsza linia")
    app.log_line("druga linia")
    assert not app.LOG_PATH.with_suffix(".log.1").exists()
    assert "druga linia" in app.LOG_PATH.read_text(encoding="utf-8")


def test_duzy_log_jest_rotowany(app):
    app.LOG_PATH.write_text("x" * (app.LOG_MAX_BYTES + 10), encoding="utf-8")
    app.log_line("po rotacji")
    backup = app.LOG_PATH.with_suffix(".log.1")
    assert backup.exists(), "stary log musi trafic do .log.1"
    assert app.LOG_PATH.stat().st_size < 1000, "nowy log zaczyna od zera"
    assert "po rotacji" in app.LOG_PATH.read_text(encoding="utf-8")


def test_druga_rotacja_nadpisuje_poprzedni_backup(app):
    app.LOG_PATH.write_text("x" * (app.LOG_MAX_BYTES + 10), encoding="utf-8")
    app.log_line("pierwsza rotacja")
    app.LOG_PATH.write_text("y" * (app.LOG_MAX_BYTES + 10), encoding="utf-8")
    app.log_line("druga rotacja")
    assert app.LOG_PATH.with_suffix(".log.1").read_text(encoding="utf-8").startswith("y")


# --------------------------------------------------------------------------- #
# konfiguracja
# --------------------------------------------------------------------------- #
def test_uszkodzony_config_daje_bezpieczne_domyslne(app):
    app.CONFIG_PATH.write_text("{ to nie jest json", encoding="utf-8")
    cfg = app.load_config()
    assert cfg["dry_run"] is True, "uszkodzony config MUSI wracac do trybu prob"
    assert cfg["action"] == "shutdown"


def test_config_bez_nowych_kluczy_dostaje_domyslne(app):
    """Starszy config nie moze wywalac programu brakiem nowego pola."""
    app.CONFIG_PATH.write_text('{"quiet_seconds": 42}', encoding="utf-8")
    cfg = app.load_config()
    assert cfg["quiet_seconds"] == 42
    assert cfg["force_close_apps"] is True
    assert cfg["required_polls"] == 3


def test_stary_config_z_martwym_kluczem_nie_wywala(app):
    """cpu_threshold zniknal z decyzji - stary plik nie moze psuc startu."""
    app.CONFIG_PATH.write_text('{"cpu_threshold": 3.0, "quiet_seconds": 42}',
                               encoding="utf-8")
    cfg = app.load_config()
    assert cfg["quiet_seconds"] == 42
    assert cfg["dry_run"] is True


def test_zapis_i_odczyt_konfiguracji(app):
    cfg = app.load_config()
    cfg["quiet_seconds"] = 123
    cfg["guard_patterns"] = ["ffmpeg", "handbrake"]
    app.save_config(cfg)
    assert app.load_config()["quiet_seconds"] == 123
    assert app.load_config()["guard_patterns"] == ["ffmpeg", "handbrake"]


def test_tryb_bojowy_zapisany_w_pliku_jest_respektowany(app):
    """dry_run=False w pliku ma dzialac - to swiadomy wybor uzytkownika.

    Bezpiecznym domyslnym stanem jest ROZBROJONY (nigdy nie dziedziczony),
    a nie wymuszony tryb prob.
    """
    app.CONFIG_PATH.write_text('{"dry_run": false}', encoding="utf-8")
    assert app.load_config()["dry_run"] is False


def test_autoarm_domyslnie_wylaczony(app):
    assert app.AUTOARM is False


def test_arm_on_start_domyslnie_wylaczone(app):
    """Bezpiecznik nr 1: uzbrojenie nie moze wlaczac sie samo bez decyzji."""
    assert app.DEFAULT_CONFIG["arm_on_start"] is False
    assert app.load_config()["arm_on_start"] is False


def test_arm_on_start_da_sie_wlaczyc(app):
    app.CONFIG_PATH.write_text('{"arm_on_start": true}', encoding="utf-8")
    assert app.load_config()["arm_on_start"] is True


# --------------------------------------------------------------------------- #
# ostrzezenie o zawieszonej sesji (regresja PID 26356, 2026-09-06)
# --------------------------------------------------------------------------- #
class _FakeLabel:
    def __init__(self) -> None:
        self.text = ""
        self.packed = False

    def config(self, **kw) -> None:
        self.text = kw.get("text", self.text)

    def pack(self, **_kw) -> None:
        self.packed = True

    def pack_forget(self) -> None:
        self.packed = False


class _FakeRoot:
    def __init__(self) -> None:
        self.bells = 0

    def bell(self) -> None:
        self.bells += 1


def _gui(app, sessions):
    """ClaudeAutoShutdown bez Tk - tylko to, czego dotyka _render_stalled."""
    gui = object.__new__(app.ClaudeAutoShutdown)
    gui.sessions = sessions
    gui.stalled_seen = {}
    gui.stalled_label = _FakeLabel()
    gui.root = _FakeRoot()
    logged: list[str] = []
    gui.logged = logged
    gui._append_log = logged.append
    return gui


class _Stub:
    """Minimalna sesja: _render_stalled czyta tylko te pola."""

    def __init__(self, session_id: str, stalled: bool, silence: float) -> None:
        self.session_id = session_id
        self.stalled = stalled
        self.silence = silence
        self.name = "claude-code-19"
        self.pid = 26356


def test_zawieszona_sesja_loguje_sie_raz(app):
    gui = _gui(app, [_Stub("s1", True, 35520.0)])
    gui._render_stalled()
    gui._render_stalled()
    gui._render_stalled()
    assert len(gui.logged) == 1
    assert "26356" in gui.logged[0]
    assert gui.stalled_label.packed is True
    assert gui.root.bells == 1


def test_ruszyla_ponownie_loguje_koniec_i_chowa_pasek(app):
    session = _Stub("s1", True, 35520.0)
    gui = _gui(app, [session])
    gui._render_stalled()
    session.stalled = False
    session.silence = 2.0
    gui._render_stalled()
    assert len(gui.logged) == 2
    assert gui.stalled_seen == {}
    assert gui.stalled_label.packed is False


def test_zamknieta_sesja_nie_generuje_wpisu_o_wznowieniu(app):
    """Sesja znika z listy (zamknieta) - to nie jest 'ruszyla', tylko koniec."""
    gui = _gui(app, [_Stub("s1", True, 35520.0)])
    gui._render_stalled()
    gui.sessions = []
    gui._render_stalled()
    assert len(gui.logged) == 1
    assert gui.stalled_seen == {}


def test_brak_zawieszonych_nie_pokazuje_paska(app):
    gui = _gui(app, [_Stub("s1", False, 10.0)])
    gui._render_stalled()
    assert gui.logged == []
    assert gui.stalled_label.packed is False
