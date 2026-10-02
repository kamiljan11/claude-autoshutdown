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


# --------------------------------------------------------------------------- #
# jezyk systemu na Linuksie (CI mialo locale C -> wychodzilo "c")
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("name", "expected"), [
    ("pl_PL.UTF-8", "pl"), ("en_GB", "en"), ("en-US", "en"), ("Polish_Poland", "pl"),
    ("C", "en"), ("C.UTF-8", "en"), ("POSIX", "en"), ("", "en"),
])
def test_kod_jezyka_z_locale(name, expected):
    from i18n import _language_code
    assert _language_code(name) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="zmienne locale tylko na POSIX")
@pytest.mark.parametrize(("env", "expected"), [
    ({"LANGUAGE": "pl:en", "LANG": "en_US.UTF-8"}, "pl"),
    ({"LANG": "pl_PL.UTF-8"}, "pl"),
    ({"LC_ALL": "C.UTF-8"}, "en"),
])
def test_jezyk_z_zmiennych_srodowiska(monkeypatch, env, expected):
    from i18n import system_language
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert system_language() == expected


# --------------------------------------------------------------------------- #
# claude_code_pids: filtr sciezek sesji (pg-review code-2)
# --------------------------------------------------------------------------- #
def _fake_proc(root, pid, exe, start=1000):
    d = root / str(pid)
    d.mkdir()
    (d / "stat").write_text(f"{pid} (x) S 1 1 1 0 -1 0 0 0 0 0 1 1 0 0 20 0 1 0 {start} 1 1",
                            encoding="utf-8")
    (d / "exe").symlink_to(exe)  # wiszacy link wystarczy - czytamy tylko readlink


def test_claude_code_pids_tylko_binarki_sesji(monkeypatch, tmp_path):
    monkeypatch.setattr(linuxprobe, "PROC_ROOT", tmp_path)
    monkeypatch.setattr(linuxprobe, "IS_LINUX", True)
    _fake_proc(tmp_path, 10, "/home/u/.config/Claude/claude-code/2.1.280/claude")   # desktop
    _fake_proc(tmp_path, 11, "/home/u/.local/share/claude/versions/2.1.283")       # CLI
    _fake_proc(tmp_path, 12, "/usr/lib/claude-desktop/claude")                      # Electron
    _fake_proc(tmp_path, 13, "/usr/bin/python3.14")
    (tmp_path / "self").mkdir()                                                     # nie-PID
    assert linuxprobe.claude_code_pids() == [10, 11]


def test_claude_code_pids_bez_proc_to_pusto(monkeypatch, tmp_path):
    monkeypatch.setattr(linuxprobe, "PROC_ROOT", tmp_path / "nie-ma")
    assert linuxprobe.claude_code_pids() == []


# --------------------------------------------------------------------------- #
# uzbrajanie: uprawnienie do WYBRANEJ akcji + mierzalna bezczynnosc (code-1, security-1)
# --------------------------------------------------------------------------- #
@pytest.fixture
def app_mod(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("CLAUDE_AUTOSHUTDOWN_HOME", str(tmp_path))
    sys.modules.pop("autoshutdown", None)
    yield importlib.import_module("autoshutdown")
    sys.modules.pop("autoshutdown", None)


def _cfg(**over):
    return {"action": "sleep", "require_human_idle": True, **over}


def test_uzbrajanie_pyta_o_wybrana_akcje(app_mod):
    asked = []
    refusal = app_mod.arm_refusal(_cfg(), capability=lambda a: asked.append(a) or (True, ""),
                                  idle_seconds=lambda: 5.0)
    assert refusal is None
    assert asked == ["sleep"], "sleep musi pytac o swoja akcje, nie o shutdown"


def test_odmowa_gdy_akcja_niedozwolona(app_mod):
    refusal = app_mod.arm_refusal(_cfg(action="hibernate"),
                                  capability=lambda _a: (False, "CanHibernate = no"),
                                  idle_seconds=lambda: 5.0)
    assert refusal == ("arm.refused_body", "CanHibernate = no")


def test_odmowa_gdy_bezczynnosc_nieznana_a_wymagana(app_mod):
    refusal = app_mod.arm_refusal(_cfg(), capability=lambda _a: (True, ""),
                                  idle_seconds=lambda: -1.0)
    assert refusal is not None and refusal[0] == app_mod.ARM_IDLE_UNKNOWN


def test_bez_wymogu_bezczynnosci_nieznana_nie_blokuje(app_mod):
    assert app_mod.arm_refusal(_cfg(require_human_idle=False), capability=lambda _a: (True, ""),
                               idle_seconds=lambda: -1.0) is None


def test_lock_nie_wola_uprawnien(app_mod):
    def boom(_a):
        raise AssertionError("lock nie potrzebuje uprawnien")

    assert app_mod.arm_refusal(_cfg(action="lock"), capability=boom,
                               idle_seconds=lambda: 5.0) is None


# --------------------------------------------------------------------------- #
# blokada instancji po restarcie (data-1)
# --------------------------------------------------------------------------- #
@LINUX_ONLY
def test_blokada_z_innego_bootu_to_smiec(app_mod):
    info = linuxprobe.probe_process(os.getpid())
    app_mod.LOCK_FILE.write_text(f"{os.getpid()} {info.created_filetime} inny-boot",
                                 encoding="utf-8")
    assert app_mod.another_instance_running() is None


@LINUX_ONLY
def test_blokada_z_tego_bootu_wykrywa_zywa_instancje(app_mod):
    app_mod.claim_instance_lock()
    assert len(app_mod.LOCK_FILE.read_text(encoding="utf-8").split()) == 3
    assert app_mod.another_instance_running() == os.getpid()


@LINUX_ONLY
def test_stary_format_blokady_dalej_dziala(app_mod):
    info = linuxprobe.probe_process(os.getpid())
    app_mod.LOCK_FILE.write_text(f"{os.getpid()} {info.created_filetime}", encoding="utf-8")
    assert app_mod.another_instance_running() == os.getpid()


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _fake_app(app_mod, cfg, **ui):
    """Atrapa okna dla save_settings: cfg = stan sprzed zapisu, ui = wartosci formularza."""
    from types import SimpleNamespace

    form = {"action": "shutdown", "dry_run": cfg["dry_run"],
            "require_human_idle": cfg["require_human_idle"],
            "allow_zero_sessions": cfg["allow_zero_sessions"],
            "force_close_apps": cfg["force_close_apps"], **ui}
    fake = SimpleNamespace(
        cfg=cfg, armed=True, countdown=None,
        vars={k: _Var(str(cfg[k])) for k in ("quiet_seconds", "poll_seconds", "required_polls",
                                              "countdown_seconds", "human_idle_required")},
        action_box=_Var(f"{form['action']} - x"), dry_var=_Var(form["dry_run"]),
        human_var=_Var(form["require_human_idle"]), zero_var=_Var(form["allow_zero_sessions"]),
        armstart_var=_Var(False), force_var=_Var(form["force_close_apps"]), guard_var=_Var(""),
        monitor=SimpleNamespace(reset_stability=lambda: None, wake=lambda: None),
        settings_status=SimpleNamespace(config=lambda **_k: None),
        root=SimpleNamespace(after=lambda *_a: None),
        _append_log=lambda _line: None, _render_header=lambda: None,
    )
    cls = app_mod.ClaudeAutoShutdown
    fake._report_arm_refusal = lambda refusal, dialog=None: cls._report_arm_refusal(
        fake, refusal, dialog)
    fake._arm_summary = lambda: cls._arm_summary(fake)
    return fake


@pytest.mark.parametrize("save_fails", [False, True])
def test_zmiana_ustawien_w_stanie_uzbrojonym_sprawdza_warunki_ponownie(app_mod, monkeypatch,
                                                                        save_fails):
    """pg-review security: odznacz bezczynnosc -> uzbroj -> zaznacz z powrotem nie moze
    zostawic uzbrojonego programu przy niemierzalnej bezczynnosci."""
    shown = []

    def save(_cfg):
        if save_fails:  # zablokowany config.json nie moze ominac ponownego sprawdzenia
            raise OSError("config.json zablokowany")

    monkeypatch.setattr(app_mod, "save_config", save)
    monkeypatch.setattr(app_mod.messagebox, "showerror", lambda *a: None)
    monkeypatch.setattr(app_mod.messagebox, "showwarning", lambda *a: shown.append(a))
    monkeypatch.setattr(app_mod.probe, "human_idle_seconds", lambda: -1.0)
    monkeypatch.setattr(app_mod.probe, "shutdown_capability", lambda _a: (True, ""))
    cfg = dict(app_mod.DEFAULT_CONFIG, require_human_idle=False)
    fake = _fake_app(app_mod, cfg, require_human_idle=True)
    app_mod.ClaudeAutoShutdown.save_settings(fake)
    assert fake.cfg["require_human_idle"] is True
    assert fake.armed is False, "niemierzalna bezczynnosc + wymog = rozbrojenie"
    assert shown, "uzytkownik musi dostac komunikat, czemu program sie rozbroil"


# --------------------------------------------------------------------------- #
# grozniejsze ustawienia przy uzbrojonym programie = ponowne potwierdzenie (2026-10-02)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("change", "expected"), [
    ({"dry_run": False}, True),                    # tryb prob -> bojowy
    ({"action": "sleep"}, True),                   # inna akcja
    ({"force_close_apps": True}, True),            # wymuszone zamykanie wlaczone
    ({"require_human_idle": False}, True),         # zdjety wymog bezczynnosci
    ({"allow_zero_sessions": True}, True),         # wylaczenie bez zadnej sesji
    ({"quiet_seconds": 900}, False),               # bezpieczniej / obojetnie
])
def test_needs_reconfirm(app_mod, change, expected):
    old = dict(app_mod.DEFAULT_CONFIG, dry_run=True, force_close_apps=False)
    assert app_mod.needs_reconfirm(old, {**old, **change}) is expected


def test_powrot_do_trybu_prob_nie_pyta(app_mod):
    old = dict(app_mod.DEFAULT_CONFIG, dry_run=False)
    assert app_mod.needs_reconfirm(old, {**old, "dry_run": True}) is False


@pytest.mark.parametrize("answer", [True, False])
def test_grozniejsza_zmiana_wymaga_potwierdzenia(app_mod, monkeypatch, answer):
    asked = []
    monkeypatch.setattr(app_mod, "save_config", lambda _cfg: None)
    monkeypatch.setattr(app_mod.messagebox, "askyesno",
                        lambda *a, **_k: asked.append(a) or answer)
    monkeypatch.setattr(app_mod.probe, "human_idle_seconds", lambda: 5.0)
    monkeypatch.setattr(app_mod.probe, "shutdown_capability", lambda _a: (True, ""))
    cfg = dict(app_mod.DEFAULT_CONFIG, dry_run=True)
    fake = _fake_app(app_mod, cfg, dry_run=False)
    app_mod.ClaudeAutoShutdown.save_settings(fake)
    assert len(asked) == 1, "tryb prob -> bojowy przy uzbrojeniu musi zapytac"
    assert fake.armed is answer, "Tak = dalej uzbrojony, Nie = rozbrojony"


def test_bezpieczna_zmiana_nie_pyta(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "save_config", lambda _cfg: None)
    monkeypatch.setattr(app_mod.messagebox, "askyesno",
                        lambda *a, **_k: pytest.fail("nie powinno pytac"))
    monkeypatch.setattr(app_mod.probe, "human_idle_seconds", lambda: 5.0)
    monkeypatch.setattr(app_mod.probe, "shutdown_capability", lambda _a: (True, ""))
    cfg = dict(app_mod.DEFAULT_CONFIG, dry_run=False)
    fake = _fake_app(app_mod, cfg, dry_run=True)
    app_mod.ClaudeAutoShutdown.save_settings(fake)
    assert fake.armed is True
