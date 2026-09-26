"""Testy backendu Linux. Zaden test NIE wylacza, nie usypia ani nie blokuje ekranu -
subprocess jest podmieniany, a /proc czytamy tylko dla wlasnego procesu."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import linuxprobe
from linuxprobe import (
    POWER_ACTIONS,
    parse_mutter_idle,
    parse_stat,
    power_action,
    shutdown_capability,
)
from monitor import SessionScanner

LINUX_ONLY = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="tylko Linux")

# Prawdziwa linia /proc/<pid>/stat (skrocona do pola 22) z comm zawierajacym spacje i nawias.
STAT_LINE = ("4242 (Web (x) Content) S 1 4242 4242 0 -1 4194560 100 0 0 0 "
             "250 50 0 0 20 0 12 0 633076 123456 789")


class FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --------------------------------------------------------------------------- #
# parsowanie
# --------------------------------------------------------------------------- #
def test_parse_stat_comm_ze_spacjami_i_nawiasami():
    assert parse_stat(STAT_LINE) == ("Web (x) Content", 633076, 300)


@pytest.mark.parametrize("raw", ["", "123 bez nawiasow S 1", "1 (x) S 1 2"])
def test_parse_stat_smieci_to_none(raw):
    assert parse_stat(raw) is None


@pytest.mark.parametrize("out,expected", [
    ("(uint64 66615,)\n", 66.615),
    ("(uint64 0,)", 0.0),
    ("", None),
])
def test_parse_mutter_idle(out, expected):
    assert parse_mutter_idle(out) == expected


# --------------------------------------------------------------------------- #
# proces - wlasny PID, bez zgadywania
# --------------------------------------------------------------------------- #
@LINUX_ONLY
def test_wlasny_proces_zyje_i_ma_czas_startu_z_proc():
    info = linuxprobe.probe_process(os.getpid())
    assert info.alive
    with open(f"/proc/{os.getpid()}/stat", encoding="utf-8") as fh:
        expected = parse_stat(fh.read())
    assert expected is not None
    assert info.created_filetime == expected[1]
    assert info.exe == os.readlink(f"/proc/{os.getpid()}/exe").removesuffix(" (deleted)")


def test_martwy_pid():
    assert not linuxprobe.probe_process(-1).alive
    assert not linuxprobe.probe_process(0).alive


@LINUX_ONLY
def test_lista_procesow_zawiera_pythona():
    names = linuxprobe.process_names()
    assert names is not None
    assert any(n.startswith("python") for n in names)


def test_brak_proc_to_none_nie_czysto(monkeypatch, tmp_path):
    """Awaria odczytu listy procesow ma blokowac, nie przepuszczac (jak tasklist)."""
    monkeypatch.setattr(linuxprobe, "PROC_ROOT", tmp_path / "nie-ma")
    assert linuxprobe.process_names() is None


def test_cpu_w_jednostkach_100ns(monkeypatch, tmp_path):
    proc = tmp_path / "4242"
    proc.mkdir()
    (proc / "stat").write_text(STAT_LINE, encoding="utf-8")
    monkeypatch.setattr(linuxprobe, "PROC_ROOT", tmp_path)
    monkeypatch.setattr(linuxprobe, "IS_LINUX", True)
    monkeypatch.setattr(linuxprobe, "CLOCK_TICKS", 100)
    info = linuxprobe.probe_process(4242)
    assert info.alive
    assert info.cpu_100ns == 3 * linuxprobe.FILETIME_PER_SECOND  # 300 tykow = 3 s
    assert info.exe == "Web (x) Content"  # brak /proc/<pid>/exe -> comm


# --------------------------------------------------------------------------- #
# rejestr sesji: procStart na Linuksie = tyki, sesja CLI z binarka "2.1.283"
# --------------------------------------------------------------------------- #
@LINUX_ONLY
def test_sesja_z_procstart_w_tykach_jest_zywa(tmp_path):
    info = linuxprobe.probe_process(os.getpid())
    scanner = SessionScanner(tmp_path)
    meta = {"pid": os.getpid(), "procStart": str(info.created_filetime)}
    fake = linuxprobe.ProcInfo(pid=info.pid, alive=True,
                               exe="/home/u/.local/share/claude/versions/2.1.283",
                               created_filetime=info.created_filetime)
    assert scanner._is_live_claude(meta, fake)
    wrong = {**meta, "procStart": str(info.created_filetime + 5 * linuxprobe.CLOCK_TICKS)}
    assert not scanner._is_live_claude(wrong, fake), "recykling PID musi byc wykryty"


def test_obcy_proces_nie_jest_sesja(tmp_path):
    scanner = SessionScanner(tmp_path)
    other = linuxprobe.ProcInfo(pid=1, alive=True, exe="/usr/bin/bash", created_filetime=7)
    assert not scanner._is_live_claude({"procStart": "7"}, other)


def test_skan_rejestru_w_formacie_linux(monkeypatch, tmp_path):
    """Ksztalt ~/.claude/sessions/<PID>.json z Linuksa (Claude Code 2.1.280):
    procStart = tyki jako napis. Skaner musi uznac sesje za zywa."""
    import monitor

    (tmp_path / "sessions").mkdir()
    record = {"pid": 72450, "sessionId": "cbd677e8", "procStart": "633076",
              "cwd": "/home/u/proj", "entrypoint": "claude-desktop", "kind": "interactive"}
    (tmp_path / "sessions" / "72450.json").write_text(json.dumps(record), encoding="utf-8")
    fake = linuxprobe.ProcInfo(pid=72450, alive=True, created_filetime=633076 + 50,
                               exe="/home/u/.config/Claude/claude-code/2.1.280/claude")
    monkeypatch.setattr(monitor, "probe_process", lambda _pid: fake)
    monkeypatch.setattr(monitor, "PROC_START_TOLERANCE", 100)  # 1 s przy CLK_TCK=100
    sessions = SessionScanner(tmp_path).scan(quiet_seconds=300)
    assert [s.pid for s in sessions] == [72450]


# --------------------------------------------------------------------------- #
# akcje zasilania - tylko ksztalt komend i obsluga kodow wyjscia
# --------------------------------------------------------------------------- #
def test_shutdown_ignoruje_blokady_jak_f_na_windows():
    assert POWER_ACTIONS["shutdown"].args == ["systemctl", "poweroff", "-i"]


def test_lock_nie_wymaga_hasla_admina():
    """lock-sessions (wszystkie) = auth_admin_keep; lock-session auto = wlasna sesja."""
    assert POWER_ACTIONS["lock"].args == ["loginctl", "lock-session", "auto"]


def test_te_same_klucze_akcji_co_windows():
    import winprobe
    assert set(POWER_ACTIONS) == set(winprobe.POWER_ACTIONS)


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(linuxprobe, "IS_LINUX", True)


def test_sukces_tylko_przy_kodzie_zero(monkeypatch, linux):
    calls = []

    def fake_run(args, **_k):
        calls.append(args)
        return FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, _ = power_action("shutdown")
    assert ok and calls == [["systemctl", "poweroff", "-i"]]


def test_bez_wymuszania_nie_ignoruje_blokad(monkeypatch, linux):
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda args, **_k: calls.append(args) or FakeCompleted(0))
    power_action("shutdown", force=False)
    assert calls == [["systemctl", "poweroff"]]


def test_odmowa_systemctl_to_porazka(monkeypatch, linux):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: FakeCompleted(
        1, stderr="Call to PowerOff failed: Access denied"))
    ok, detail = power_action("shutdown")
    assert not ok
    assert "Access denied" in detail


def test_timeout_to_porazka(monkeypatch, linux):
    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=25)

    monkeypatch.setattr(subprocess, "run", slow)
    ok, _ = power_action("sleep")
    assert not ok


def test_nothing_nic_nie_uruchamia(monkeypatch, linux):
    def boom(*_a, **_k):
        raise AssertionError("nic nie powinno sie uruchomic")

    monkeypatch.setattr(subprocess, "run", boom)
    ok, _ = power_action("nothing")
    assert ok


# --------------------------------------------------------------------------- #
# uprawnienia z logind
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("answer,ok", [("yes", True), ("no", False), ("na", False),
                                       ("challenge", False), ("", False)])
def test_capability_z_logind(monkeypatch, linux, answer, ok):
    seen = []

    def fake_can(method):
        seen.append(method)
        return answer

    monkeypatch.setattr(linuxprobe, "logind_can", fake_can)
    assert shutdown_capability("hibernate")[0] is ok
    assert seen == ["CanHibernate"], "kazda akcja pyta o SWOJA metode Can*"


def test_lock_nie_pyta_logind(monkeypatch, linux):
    monkeypatch.setattr(linuxprobe, "logind_can",
                        lambda _m: (_ for _ in ()).throw(AssertionError("zbedne")))
    assert shutdown_capability("lock")[0]


def test_logind_can_parsuje_busctl(monkeypatch):
    monkeypatch.setattr(linuxprobe, "_query", lambda _a: 's "challenge"\n')
    assert linuxprobe.logind_can("CanPowerOff") == "challenge"
    monkeypatch.setattr(linuxprobe, "_query", lambda _a: None)
    assert linuxprobe.logind_can("CanPowerOff") == ""


def test_idle_nieznane_gdy_brak_narzedzi(monkeypatch, linux):
    monkeypatch.setattr(linuxprobe, "_query", lambda _a: None)
    assert linuxprobe.human_idle_seconds() == -1.0


def test_idle_z_mutter(monkeypatch, linux):
    monkeypatch.setattr(linuxprobe, "_query", lambda a: "(uint64 1500,)" if a[0] == "gdbus"
                        else None)
    assert linuxprobe.human_idle_seconds() == 1.5


def test_idle_fallback_xprintidle(monkeypatch, linux):
    monkeypatch.setattr(linuxprobe, "_query", lambda a: "2500\n" if a[0] == "xprintidle"
                        else None)
    assert linuxprobe.human_idle_seconds() == 2.5
