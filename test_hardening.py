"""Testy z drugiej rundy audytu (findingi medium/low).

Kazdy test odpowiada jednemu znalezisku z docs/audit-findings.json i opisuje,
jaki zly skutek mial finding, zeby regresja byla od razu zrozumiala.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from i18n import missing_keys, t
from monitor import (
    TURN_CLOSED,
    TURN_OPEN,
    Session,
    evaluate,
    matching_guard_processes,
    tail_events,
)

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="tylko Windows")


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAUDE_AUTOSHUTDOWN_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_AUTOSHUTDOWN_AUTOARM", raising=False)
    sys.modules.pop("autoshutdown", None)
    module = importlib.import_module("autoshutdown")
    yield module
    sys.modules.pop("autoshutdown", None)


def make_session(**overrides) -> Session:
    defaults = {
        "pid": 1, "session_id": "s", "name": "claude-code-xx", "cwd": r"C:\proj",
        "entrypoint": "claude-desktop", "kind": "interactive", "started_at": 0.0,
        "transcript": None, "last_activity": time.time() - 1000, "silence": 1000.0,
        "cpu_percent": 0.0, "active_subagents": 0, "working": False,
        "turn": TURN_CLOSED, "turn_reason": "turn.waiting_for_you",
    }
    defaults.update(overrides)
    return Session(**defaults)


BASE = {
    "quiet_seconds": 300.0, "armed": True, "stop_file_present": False,
    "human_idle": 9999.0, "require_human_idle": True, "human_idle_required": 600.0,
    "allow_zero_sessions": False, "saw_any_session": True, "guard_patterns": [],
    "guard_hits": [], "stable_polls": 2, "required_polls": 3,
}


# --------------------------------------------------------------------------- #
# konfiguracja: zly plik nie moze cicho wlaczyc niebezpiecznych opcji
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["false", "no", "0", "off", "", False, 0])
def test_string_false_nie_wlacza_allow_zero_sessions(app, value):
    """bool("false") == True - najgrozniejsza opcja wlaczala sie od napisu."""
    cfg, _ = app.validate_config({"allow_zero_sessions": value})
    assert cfg["allow_zero_sessions"] is False


@pytest.mark.parametrize("value", ["true", "yes", "1", "on", True, 1])
def test_string_true_wlacza_bool(app, value):
    cfg, _ = app.validate_config({"dry_run": value})
    assert cfg["dry_run"] is True


def test_niezrozumialy_bool_wraca_do_domyslnego_z_ostrzezeniem(app):
    cfg, warnings = app.validate_config({"dry_run": "maybe"})
    assert cfg["dry_run"] is True
    assert any("dry_run" in w for w in warnings)


def test_nieznana_akcja_to_nothing_nie_shutdown(app):
    """Literowka w 'action' nie moze zamienic sie w 'wylacz komputer'."""
    cfg, warnings = app.validate_config({"action": "shutdwon"})
    assert cfg["action"] == "nothing"
    assert any("action" in w for w in warnings)


def test_countdown_zero_jest_przycinany(app):
    """countdown 0 = akcja natychmiast, ANULUJ nie zdazy istniec."""
    cfg, warnings = app.validate_config({"countdown_seconds": 0})
    assert cfg["countdown_seconds"] >= 5
    assert warnings


@pytest.mark.parametrize("raw", ["ffmpeg, handbrake", ["ffmpeg", "handbrake"]])
def test_guard_patterns_string_nie_rozpada_sie_na_litery(app, raw):
    cfg, _ = app.validate_config({"guard_patterns": raw})
    assert cfg["guard_patterns"] == ["ffmpeg", "handbrake"]


def test_guard_patterns_smiec_ignorowany(app):
    cfg, warnings = app.validate_config({"guard_patterns": 42})
    assert cfg["guard_patterns"] == []
    assert warnings


def test_config_w_ansi_nie_wywala_startu(app):
    app.CONFIG_PATH.write_bytes('{"quiet_seconds": 42, "x": "zażółć"}'.encode("cp1250"))
    cfg = app.load_config()
    assert cfg["dry_run"] is True
    assert app.CONFIG_WARNINGS, "nieczytelny plik musi zostawic ostrzezenie"


def test_config_lista_zamiast_obiektu_nie_wywala(app):
    app.CONFIG_PATH.write_text("[1, 2, 3]", encoding="utf-8")
    cfg = app.load_config()
    assert cfg["action"] == "shutdown"
    assert any("obiektem" in w for w in app.CONFIG_WARNINGS)


def test_zapis_konfiguracji_jest_atomowy(app):
    cfg = app.load_config()
    app.save_config(cfg)
    assert not app.CONFIG_PATH.with_suffix(".json.tmp").exists()
    assert json.loads(app.CONFIG_PATH.read_text(encoding="utf-8"))["action"] == "shutdown"


@pytest.mark.parametrize("name", ["STOP", "STOP.txt", "stop.txt", "stop"])
def test_stop_z_rozszerzeniem_tez_hamuje(app, name):
    """Eksplorator dokleja .txt po cichu - hamulec musial to przyjac."""
    (app.APP_DIR / name).write_text("", encoding="utf-8")
    assert app.stop_file_present() is True


def test_brak_stop(app):
    assert app.stop_file_present() is False


def test_katalog_stanu_zapisywalny(app):
    assert app.state_dir_writable() is True


# --------------------------------------------------------------------------- #
# straznicy: awaria skanu = blokada, nie "czysto"
# --------------------------------------------------------------------------- #
def test_awaria_tasklist_blokuje():
    verdict = evaluate([make_session()], **{**BASE, "guard_patterns": ["ffmpeg"],
                                            "guard_hits": None})
    assert not verdict.ok
    assert any("process" in b.lower() or "proces" in b.lower() for b in verdict.blockers)


def test_bez_wzorcow_awaria_nie_ma_znaczenia():
    verdict = evaluate([make_session()], **{**BASE, "guard_patterns": [], "guard_hits": None})
    assert verdict.ok


@WINDOWS_ONLY
def test_tasklist_awaria_zwraca_none(monkeypatch):
    def explode(*_a, **_k):
        raise OSError("tasklist nie istnieje")

    monkeypatch.setattr(subprocess, "run", explode)
    assert matching_guard_processes(["ffmpeg"]) is None


# --------------------------------------------------------------------------- #
# evaluate: brakujace testy z audytu (56, 57)
# --------------------------------------------------------------------------- #
def test_otwarta_tura_blokuje_niezaleznie_od_working():
    """Check 'kazda tura domknieta' w izolacji: working=False, ale tura OPEN."""
    s = make_session(working=False, turn=TURN_OPEN, turn_reason="turn.tool_in_flight")
    verdict = evaluate([s], **BASE)
    assert not verdict.ok
    assert any(t("check.turns_closed") in b for b in verdict.blockers)


def test_sesje_zniknely_po_tym_jak_byly_wymaga_ciszy():
    """Galaz zero-sesji NIE moze kasowac warunku ciszy."""
    fresh = evaluate([], **{**BASE, "saw_any_session": True,
                            "seconds_since_last_session": 30.0})
    assert not fresh.ok
    old = evaluate([], **{**BASE, "saw_any_session": True,
                          "seconds_since_last_session": 3000.0})
    assert old.ok


# --------------------------------------------------------------------------- #
# podglad: filtr szumu + okno adaptacyjne
# --------------------------------------------------------------------------- #
def test_tail_events_pomija_rekordy_techniczne(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    records = [{"type": "attachment"}] * 30 + [{"type": "assistant", "n": 1},
                                               {"type": "frame-link"},
                                               {"type": "user", "n": 2}]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = tail_events(path, count=10)
    assert [e.get("type") for e in events] == ["assistant", "user"]


def test_tail_events_rekord_wiekszy_niz_okno(tmp_path: Path):
    path = tmp_path / "big.jsonl"
    huge = {"type": "user", "message": {"content": [{"type": "tool_result",
                                                     "text": "x" * 300_000}]}}
    path.write_text(json.dumps({"type": "assistant"}) + "\n" + json.dumps(huge) + "\n",
                    encoding="utf-8")
    events = tail_events(path, count=5, max_bytes=64 * 1024)
    assert events, "duzy rekord nie moze dawac pustego podgladu"
    assert events[-1]["type"] == "user"


# --------------------------------------------------------------------------- #
# winprobe: timeout = porazka, parser powercfg
# --------------------------------------------------------------------------- #
@WINDOWS_ONLY
def test_timeout_akcji_to_porazka(monkeypatch):
    """subprocess.run po timeoucie ZABIJA dziecko - meldowanie sukcesu klamalo."""
    import winprobe

    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="shutdown", timeout=25)

    monkeypatch.setattr(subprocess, "run", slow)
    ok, detail = winprobe.power_action("shutdown")
    assert not ok
    assert "25" in detail


@WINDOWS_ONLY
def test_powercfg_nie_pokazuje_stanow_niedostepnych(monkeypatch):
    import winprobe

    class Fake:
        stdout = ("The following sleep states are available on this system:\n"
                  "    Standby (S3)\n    Hibernate\n\n"
                  "The following sleep states are not available on this system:\n"
                  "    Standby (S1)\n        The system firmware does not support this.\n"
                  "    Hybrid Sleep\n")

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: Fake())
    states = winprobe.available_sleep_states()
    assert "Hibernate" in states
    assert "S1" not in states and "Hybrid" not in states


def test_sleep_uzywa_api_zamiast_rundll(monkeypatch):
    """rundll32 gubil argumenty '0,1,0' - akcja sleep idzie przez SetSuspendState."""
    import winprobe

    called: list[tuple] = []

    class FakePowrprof:
        @staticmethod
        def SetSuspendState(*args):
            called.append(args)
            return 1

    class FakeWindll:
        powrprof = FakePowrprof()

    monkeypatch.setattr(winprobe.ctypes, "windll", FakeWindll(), raising=False)
    monkeypatch.setattr(winprobe, "IS_WINDOWS", True)
    ok, detail = winprobe.power_action("sleep")
    assert ok
    assert "SetSuspendState" in detail
    for _ in range(50):
        if called:
            break
        time.sleep(0.02)
    assert called == [(0, 0, 0)]


# --------------------------------------------------------------------------- #
# i18n: nowe klucze w obu jezykach
# --------------------------------------------------------------------------- #
def test_nowe_klucze_maja_oba_jezyki():
    assert missing_keys() == {"pl": []}
    for key in ("countdown.reason.settings", "check.no_guards.scan_failed",
                "power.timeout", "crash.title", "state.not_writable"):
        assert t(key) != key
