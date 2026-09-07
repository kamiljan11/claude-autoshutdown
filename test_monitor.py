"""Testy silnika decyzyjnego. Uruchom: python -m pytest -q  (albo python test_monitor.py)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from i18n import t
from monitor import (
    STALLED_TURN_SECONDS,
    TURN_CLOSED,
    TURN_OPEN,
    TURN_UNKNOWN,
    Session,
    classify_turn,
    evaluate,
    fmt_duration,
    is_working,
    tail_events,
    turn_state,
)

BASE_ARGS = {
    "quiet_seconds": 300.0,
    "armed": True,
    "stop_file_present": False,
    "human_idle": 9999.0,
    "require_human_idle": True,
    "human_idle_required": 600.0,
    "allow_zero_sessions": False,
    "saw_any_session": True,
    "guard_patterns": [],
    "guard_hits": [],
    "stable_polls": 2,
    "required_polls": 3,
}


def make_session(**overrides) -> Session:
    defaults = {
        "pid": 1234, "session_id": "abc", "name": "claude-code-xx", "cwd": r"C:\proj",
        "entrypoint": "claude-desktop", "kind": "interactive", "started_at": 0.0,
        "transcript": None, "last_activity": time.time() - 1000, "silence": 1000.0,
        "cpu_percent": 0.0, "active_subagents": 0, "working": False,
        "turn": TURN_CLOSED, "turn_reason": "turn.waiting_for_you",
    }
    defaults.update(overrides)
    return Session(**defaults)


# --------------------------------------------------------------------------- #
# evaluate()
# --------------------------------------------------------------------------- #
def test_wszystko_czyste_wylacza():
    verdict = evaluate([make_session()], **BASE_ARGS)
    assert verdict.ok
    assert verdict.stable_polls == 3


def test_pracujaca_sesja_blokuje_i_zeruje_licznik():
    busy = make_session(working=True, silence=5.0, turn=TURN_OPEN,
                        turn_reason="turn.tool_in_flight")
    verdict = evaluate([busy], **BASE_ARGS)
    assert not verdict.ok
    assert verdict.stable_polls == 0, "licznik potwierdzen musi wrocic do zera"
    assert any(t("turn.tool_in_flight") in b for b in verdict.blockers)


def test_otwarta_tura_blokuje_mimo_dlugiej_ciszy():
    """Kompaktowanie / czekanie na limit API: plik stoi, ale praca trwa."""
    compacting = make_session(working=True, silence=3600.0, turn=TURN_OPEN,
                              turn_reason="turn.compacting")
    verdict = evaluate([compacting], **BASE_ARGS)
    assert not verdict.ok
    assert any(t("turn.compacting") in b for b in verdict.blockers)


def test_krotka_cisza_blokuje_nawet_gdy_tura_domknieta():
    fresh = make_session(silence=10.0)
    verdict = evaluate([fresh], **BASE_ARGS)
    assert not verdict.ok


def test_rozbrojony_nigdy_nie_wylacza():
    verdict = evaluate([make_session()], **{**BASE_ARGS, "armed": False})
    assert not verdict.ok


def test_plik_stop_blokuje():
    verdict = evaluate([make_session()], **{**BASE_ARGS, "stop_file_present": True})
    assert not verdict.ok
    assert any("STOP" in b for b in verdict.blockers)


def test_aktywny_uzytkownik_blokuje():
    verdict = evaluate([make_session()], **{**BASE_ARGS, "human_idle": 5.0})
    assert not verdict.ok


def test_nieznana_bezczynnosc_uzytkownika_nie_blokuje():
    """human_idle < 0 = nie umiemy zmierzyc; nie blokujemy w nieskonczonosc."""
    verdict = evaluate([make_session()], **{**BASE_ARGS, "human_idle": -1.0})
    assert verdict.ok


def test_proces_straznik_blokuje():
    verdict = evaluate([make_session()], **{**BASE_ARGS,
                                            "guard_patterns": ["ffmpeg"],
                                            "guard_hits": ["ffmpeg.exe"]})
    assert not verdict.ok
    assert any("ffmpeg.exe" in b for b in verdict.blockers)


def test_zero_sesji_domyslnie_blokuje():
    verdict = evaluate([], **{**BASE_ARGS, "saw_any_session": False})
    assert not verdict.ok


def test_zero_sesji_dozwolone_gdy_wlaczone():
    verdict = evaluate([], **{**BASE_ARGS, "saw_any_session": False,
                              "allow_zero_sessions": True})
    assert verdict.ok


def test_potrzeba_wymaganej_liczby_potwierdzen():
    verdict = evaluate([make_session()], **{**BASE_ARGS, "stable_polls": 0})
    assert not verdict.ok
    assert verdict.stable_polls == 1


def test_jedna_pracujaca_wsrod_wielu_blokuje():
    sessions = [make_session(session_id=str(i)) for i in range(4)]
    sessions[2] = make_session(session_id="2", working=True, silence=2.0,
                               turn=TURN_OPEN, turn_reason="turn.tool_in_flight")
    verdict = evaluate(sessions, **BASE_ARGS)
    assert not verdict.ok


# --------------------------------------------------------------------------- #
# classify_turn()
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("event", "expected"), [
    ({"type": "assistant", "message": {"stop_reason": "end_turn"}}, TURN_CLOSED),
    ({"type": "assistant", "message": {"stop_reason": "stop_sequence"}}, TURN_CLOSED),
    ({"type": "assistant", "message": {"stop_reason": "tool_use"}}, TURN_OPEN),
    ({"type": "assistant", "message": {"stop_reason": "max_tokens"}}, TURN_OPEN),
    ({"type": "user", "message": {"content": [{"type": "tool_result"}]}}, TURN_OPEN),
    ({"type": "user", "message": {"content": "nowy prompt"}}, TURN_OPEN),
    ({"type": "user", "isCompactSummary": True, "message": {"content": "..."}}, TURN_OPEN),
    ({"type": "cos-nowego"}, TURN_UNKNOWN),
])
def test_classify_turn(event, expected):
    state, reason, _params = classify_turn(event)
    assert state == expected
    assert reason.startswith("turn."), "powod musi byc kluczem i18n"
    assert t(reason) != reason, "klucz musi miec tlumaczenie"


def test_classify_turn_kompaktowanie_ma_wlasny_opis():
    _, reason, _p = classify_turn({"type": "user", "isCompactSummary": True})
    assert reason == "turn.compacting"


# --------------------------------------------------------------------------- #
# turn_state() / tail_events() na prawdziwym pliku
# --------------------------------------------------------------------------- #
def write_transcript(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_turn_state_pomija_rekordy_techniczne(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    write_transcript(path, [
        {"type": "assistant", "message": {"stop_reason": "end_turn"}},
        {"type": "attachment"},
        {"type": "bridge-session"},
        {"type": "custom-title"},
    ])
    state, _, _ = turn_state(path)
    assert state == TURN_CLOSED, "szum po ostatniej turze nie moze zmienic werdyktu"


def test_turn_state_wykrywa_otwarte_narzedzie(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    write_transcript(path, [
        {"type": "assistant", "message": {"stop_reason": "end_turn"}},
        {"type": "user", "message": {"content": "zrob cos"}},
        {"type": "assistant", "message": {"stop_reason": "tool_use"}},
    ])
    state, reason, _ = turn_state(path)
    assert state == TURN_OPEN
    assert reason == "turn.tool_in_flight"


def test_turn_state_brak_pliku():
    assert turn_state(None)[0] == TURN_UNKNOWN
    assert turn_state(Path("nie-istnieje-12345.jsonl"))[0] == TURN_UNKNOWN


def test_turn_state_czyta_tylko_ogon(tmp_path: Path):
    """Duzy plik: czytamy koncowke, a i tak dostajemy poprawny werdykt."""
    path = tmp_path / "big.jsonl"
    filler = [{"type": "assistant", "message": {"stop_reason": "tool_use",
                                                "content": [{"type": "text",
                                                             "text": "x" * 500}]}}
              for _ in range(500)]
    write_transcript(path, [*filler, {"type": "assistant",
                                      "message": {"stop_reason": "end_turn"}}])
    assert path.stat().st_size > 256 * 1024
    assert turn_state(path, max_bytes=64 * 1024)[0] == TURN_CLOSED


def test_turn_state_ignoruje_urwana_pierwsza_linie(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    path.write_bytes(b'{"type": "assist' + b"x" * 200 + b"\n"
                     + json.dumps({"type": "assistant",
                                   "message": {"stop_reason": "end_turn"}}).encode()
                     + b"\n")
    assert turn_state(path, max_bytes=64)[0] in (TURN_CLOSED, TURN_UNKNOWN)


def test_tail_events_zwraca_ostatnie(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    write_transcript(path, [{"type": "assistant", "n": i} for i in range(50)])
    events = tail_events(path, count=5)
    assert len(events) == 5
    assert events[-1]["n"] == 49


# --------------------------------------------------------------------------- #
# drobiazgi
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("seconds", "expected"), [
    (0, "0s"), (45, "45s"), (60, "1m 00s"), (125, "2m 05s"), (3700, "1h 01m"),
    (float("inf"), "-"),
])
def test_fmt_duration(seconds, expected):
    assert fmt_duration(seconds) == expected


def test_session_why_tlumaczy_stan():
    assert make_session(working=True, turn=TURN_OPEN,
                        turn_reason="turn.compacting").why == t("turn.compacting")
    assert "subagent" in make_session(working=True, turn=TURN_UNKNOWN,
                                      active_subagents=2).why


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------- #
# regresja: pomiar CPU przy krotkim odstepie miedzy skanami
# --------------------------------------------------------------------------- #
def test_cpu_percent_odrzuca_zbyt_krotki_odstep():
    """Dwa skany tuz po sobie nie moga dac fałszywego "sesja zuzywa CPU".

    Bez tego proces, ktory tylko wystartowal, pokazywal kilkadziesiat procent
    i sesja zostawala na zawsze w stanie PRACUJE (komputer nigdy sie nie wylaczy).
    """
    from monitor import SessionScanner
    from winprobe import FILETIME_PER_SECOND, ProcInfo

    scanner = SessionScanner(root=Path("."))
    pid = 4242
    # pierwszy odczyt ustawia punkt odniesienia
    assert scanner._cpu_percent(ProcInfo(pid=pid, alive=True, cpu_100ns=0)) == 0.0
    # drugi odczyt natychmiast po nim: 0,2 s CPU w ~0 s realnego czasu
    burst = ProcInfo(pid=pid, alive=True, cpu_100ns=int(0.2 * FILETIME_PER_SECOND))
    assert scanner._cpu_percent(burst) == 0.0, "krotki odstep musi byc odrzucony"


def test_cpu_percent_liczy_gdy_odstep_wystarczajacy():
    from monitor import SessionScanner
    from winprobe import FILETIME_PER_SECOND, ProcInfo

    scanner = SessionScanner(root=Path("."))
    pid = 4243
    scanner._cpu_percent(ProcInfo(pid=pid, alive=True, cpu_100ns=0))
    # cofamy punkt odniesienia o 10 s, jakby minal pelny cykl skanowania
    cpu_prev, when = scanner._cpu_prev[pid]
    scanner._cpu_prev[pid] = (cpu_prev, when - 10.0)
    used = ProcInfo(pid=pid, alive=True, cpu_100ns=int(2.0 * FILETIME_PER_SECOND))
    assert 15.0 < scanner._cpu_percent(used) < 25.0, "2 s CPU w 10 s to ~20%"


def test_subagenci_workflow_sa_widoczni(tmp_path: Path):
    """Regresja: agenci Workflow leza w podkatalogu subagents/workflows/wf_*/.

    Plaskie glob("*.jsonl") ich nie widzialo, wiec sesja z pracujacym workflow
    wygladala na bezczynna - a to wlasnie te agenty miela najdluzej.
    """
    from monitor import SessionScanner

    transcript = tmp_path / "sesja.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    deep = tmp_path / "sesja" / "subagents" / "workflows" / "wf_abc123"
    deep.mkdir(parents=True)
    (deep / "agent-a1.jsonl").write_text("{}\n", encoding="utf-8")

    newest, active = SessionScanner._subagent_activity(transcript)
    assert active == 1, "subagent workflow musi byc policzony"
    assert newest > 0


def test_subagenci_plascy_nadal_widoczni(tmp_path: Path):
    from monitor import SessionScanner

    transcript = tmp_path / "sesja.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    flat = tmp_path / "sesja" / "subagents"
    flat.mkdir(parents=True)
    (flat / "agent-b1.jsonl").write_text("{}\n", encoding="utf-8")
    (flat / "agent-b2.jsonl").write_text("{}\n", encoding="utf-8")

    _, active = SessionScanner._subagent_activity(transcript)
    assert active == 2


def test_stary_subagent_nie_liczy_sie_jako_aktywny(tmp_path: Path):
    import os

    from monitor import SUBAGENT_ACTIVE_WINDOW, SessionScanner

    transcript = tmp_path / "sesja.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    sub = tmp_path / "sesja" / "subagents"
    sub.mkdir(parents=True)
    old_file = sub / "agent-stary.jsonl"
    old_file.write_text("{}\n", encoding="utf-8")
    old = time.time() - (SUBAGENT_ACTIVE_WINDOW + 60)
    os.utime(old_file, (old, old))

    newest, active = SessionScanner._subagent_activity(transcript)
    assert active == 0, "subagent sprzed okna aktywnosci nie blokuje wylaczenia"
    assert newest > 0, "ale jego mtime nadal liczy sie jako ostatnia aktywnosc"


IDLE = {"silence": 3600.0, "quiet_seconds": 300.0, "active_subagents": 0}


def test_nieznany_stan_tury_zawsze_blokuje():
    """Niewiedza NIE jest zgoda na wylaczenie.

    Awaria odczytu, plik zablokowany przez zapis, nieznany format rekordu -
    kazde z tego wczesniej wygladalo jak "sesja skonczyla prace".
    """
    assert is_working(**{**IDLE, "turn": TURN_UNKNOWN}) is True
    assert is_working(**{**IDLE, "turn": TURN_UNKNOWN, "silence": 999999.0}) is True


def test_otwarta_tura_zawsze_pracuje():
    assert is_working(**{**IDLE, "turn": TURN_OPEN}) is True


def test_swiezy_subagent_zawsze_pracuje():
    assert is_working(**{**IDLE, "turn": TURN_CLOSED, "active_subagents": 1}) is True


def test_krotka_cisza_to_praca():
    assert is_working(**{**IDLE, "turn": TURN_CLOSED, "silence": 10.0}) is True


def test_domknieta_tura_i_cisza_to_spoczynek():
    assert is_working(**{**IDLE, "turn": TURN_CLOSED}) is False


# --------------------------------------------------------------------------- #
# regresje z audytu adwersaryjnego (2026-09-02)
# --------------------------------------------------------------------------- #
def test_rekord_wiekszy_niz_okno_nie_daje_unknown(tmp_path: Path):
    """W zywych transkryptach jest 494 rekordow >256 KB, najwiekszy 5,1 MB.

    Prawie wszystkie to `user` z wynikiem narzedzia - czyli dokladnie moment,
    w ktorym model dostal wynik i liczy dalej. Przy stalym oknie odczytu ogon
    nie zawieral ANI JEDNEJ kompletnej linii -> UNKNOWN -> przepustka.
    """
    path = tmp_path / "big.jsonl"
    huge = {"type": "user", "message": {"content": [{"type": "tool_result",
                                                     "text": "x" * 400_000}]}}
    write_transcript(path, [{"type": "assistant",
                             "message": {"stop_reason": "end_turn"}}, huge])
    assert path.stat().st_size > 256 * 1024
    state, reason, _ = turn_state(path, max_bytes=64 * 1024)
    assert state == TURN_OPEN, f"dostalem {state}: {reason}"


def test_szum_po_gigantycznym_rekordzie_tez_nie_gubi_tury(tmp_path: Path):
    path = tmp_path / "big.jsonl"
    huge = {"type": "user", "message": {"content": [{"type": "tool_result",
                                                     "text": "x" * 400_000}]}}
    write_transcript(path, [huge, {"type": "attachment"}, {"type": "frame-link"}])
    assert turn_state(path, max_bytes=64 * 1024)[0] == TURN_OPEN


def test_adaptacja_ma_limit(tmp_path: Path):
    """Bez gornego limitu program wciagnalby caly 200 MB plik do pamieci."""
    path = tmp_path / "smieci.jsonl"
    path.write_bytes(b"x" * 300_000 + b"\n")
    state, _, _ = turn_state(path, max_bytes=1024, limit_bytes=8192)
    assert state == TURN_UNKNOWN


@pytest.mark.parametrize("noise_type", [
    "frame-link", "pr-link", "artifact-comment-monitor", "permission-mode",
    "typ-ktorego-jeszcze-nie-ma",
])
def test_nowe_typy_rekordow_nie_kasuja_otwartej_tury(tmp_path: Path, noise_type: str):
    """Lista DOZWOLONYCH zamiast listy szumu.

    Typy frame-link (946 wystapien), pr-link (155) i artifact-* (30) istnieja
    w zywych transkryptach i nie bylo ich w pierwotnej liscie szumu - kazdy
    kasowal wykryta otwarta ture i dawal UNKNOWN.
    """
    path = tmp_path / "s.jsonl"
    write_transcript(path, [
        {"type": "assistant", "message": {"stop_reason": "tool_use"}},
        {"type": noise_type, "cokolwiek": 1},
    ])
    assert turn_state(path)[0] == TURN_OPEN


def test_evaluate_blokuje_gdy_tura_nieznana():
    unknown = make_session(working=True, turn=TURN_UNKNOWN,
                           turn_reason="turn.cannot_read",
                           turn_params={"error": "Permission denied"})
    verdict = evaluate([unknown], **BASE_ARGS)
    assert not verdict.ok
    assert any("Permission denied" in b for b in verdict.blockers), verdict.blockers


@pytest.mark.parametrize(("pattern", "name", "expected"), [
    ("ffmpeg", "ffmpeg.exe", True),
    ("ffmpeg*", "ffmpeg.exe", True),        # glob - jako regex NIE trafialby
    ("*mpeg*", "ffmpeg.exe", True),
    (r".*mpeg\.exe", "ffmpeg.exe", True),   # prawdziwy regex
    ("handbrake", "ffmpeg.exe", False),
    ("[", "ffmpeg.exe", False),             # niepoprawny regex nie moze wywalic
    ("", "ffmpeg.exe", False),
])
def test_wzorce_straznikow(pattern, name, expected):
    """`ffmpeg*` jest legalnym regexem (zero lub wiecej `g`), wiec nie rzucal bledu,
    tylko po cichu nie trafial w `ffmpeg.exe` - straznik swiecil na zielono."""
    from monitor import _pattern_matches
    assert _pattern_matches(pattern, name) is expected


def test_blad_skanera_blokuje_wylaczenie():
    """Awaria skanera dawala pusta liste sesji, ktora przechodzila kazdy warunek."""
    verdict = evaluate([make_session()], **{**BASE_ARGS,
                                            "scan_error": "brak katalogu sessions"})
    assert not verdict.ok
    assert any("sessions" in b for b in verdict.blockers)


def test_zniknieta_ostatnia_sesja_wymaga_ciszy():
    """Zamkniecie ostatniego okna Claude nie moze gasic komputera po 30 s."""
    swiezo = evaluate([], **{**BASE_ARGS, "seconds_since_last_session": 5.0})
    assert not swiezo.ok
    po_czasie = evaluate([], **{**BASE_ARGS, "seconds_since_last_session": 999.0})
    assert po_czasie.ok


def test_dlugo_otwarta_tura_jest_widoczna():
    s = make_session(working=True, turn=TURN_OPEN, turn_reason="turn.tool_in_flight",
                     silence=7200.0)
    assert s.why == t("why.blocking_since", reason=t("turn.tool_in_flight"),
                      duration=fmt_duration(7200.0))


def test_proces_sesji_bez_wpisu_w_rejestrze_blokuje():
    """Sesja, ktorej nie ma w rejestrze, bylaby dla monitora niewidzialna."""
    verdict = evaluate([make_session()], **{**BASE_ARGS, "unregistered_pids": [4242]})
    assert not verdict.ok
    assert any("4242" in b for b in verdict.blockers)


def test_subagent_okno_aktywnosci_rosnie_z_progiem_ciszy(tmp_path: Path):
    """Subagent czekajacy na odpowiedz API milczy minutami, a nadal pracuje."""
    import os

    from monitor import SessionScanner

    transcript = tmp_path / "s.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    sub = tmp_path / "s" / "subagents"
    sub.mkdir(parents=True)
    f = sub / "agent-a.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    old = time.time() - 200          # wiecej niz sztywne 120 s
    os.utime(f, (old, old))

    assert SessionScanner._subagent_activity(transcript)[1] == 0, "domyslne okno 120 s"
    assert SessionScanner._subagent_activity(transcript, 300.0)[1] == 1


def test_kontrola_krzyzowa_tylko_dla_prawdziwego_rejestru(tmp_path: Path):
    """Przy podmienionym katalogu kazda zywa sesja wygladalaby jak 'proces bez wpisu'."""
    from monitor import SessionScanner, claude_dir
    assert SessionScanner(root=tmp_path).is_default_root is False
    assert SessionScanner(root=claude_dir()).is_default_root is (
        claude_dir() == Path(os.path.expanduser("~")) / ".claude")


def test_znikajacy_plik_sesji_to_nie_awaria(tmp_path: Path):
    """Zamkniecie okna Claude usuwa sessions/<pid>.json miedzy glob a odczytem.

    Liczone jako blad skanera zerowaloby licznik potwierdzen przy kazdej
    zamknietej sesji - a to zdarzenie calkiem normalne.
    """
    from monitor import SessionScanner

    (tmp_path / "sessions").mkdir()
    (tmp_path / "projects").mkdir()
    scanner = SessionScanner(root=tmp_path)

    real_read = Path.read_text

    def znika(self, *a, **kw):
        if self.name.endswith(".json"):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_read(self, *a, **kw)

    (tmp_path / "sessions" / "4242.json").write_text("{}", encoding="utf-8")
    Path.read_text = znika
    try:
        sessions = scanner.scan(quiet_seconds=300.0)
    finally:
        Path.read_text = real_read
    assert sessions == []
    assert scanner.last_error == "", f"nie powinno byc bledu, jest: {scanner.last_error}"


def test_uszkodzony_plik_sesji_TO_awaria(tmp_path: Path):
    """Dla odmiany: plik, ktory ISTNIEJE, ale jest nieczytelny, musi byc bledem."""
    from monitor import SessionScanner

    (tmp_path / "sessions").mkdir()
    (tmp_path / "projects").mkdir()
    (tmp_path / "sessions" / "4243.json").write_text("{ to nie jest json",
                                                     encoding="utf-8")
    scanner = SessionScanner(root=tmp_path)
    scanner.scan(quiet_seconds=300.0)
    assert scanner.last_error, "uszkodzony plik musi ustawic blad skanera"


def test_kazdy_jezyk_ma_komplet_kluczy():
    """Brak klucza = na ekranie pojawia sie surowy identyfikator zamiast tekstu."""
    from i18n import missing_keys
    assert missing_keys() == {"pl": []}, missing_keys()


def test_nieznany_jezyk_wraca_do_angielskiego():
    from i18n import current_language, set_language
    assert set_language("xx") == "en"
    assert current_language() == "en"


def test_przelaczenie_jezyka_zmienia_teksty_silnika():
    from i18n import set_language
    try:
        set_language("pl")
        assert make_session().state == "BEZCZYNNA"
        set_language("en")
        assert make_session().state == "IDLE"
    finally:
        set_language("en")


def test_jezyk_systemu_jest_dwuliterowym_kodem():
    from i18n import system_language
    code = system_language()
    assert len(code) == 2 and code.isalpha() and code.islower(), code


@pytest.mark.parametrize(("setting", "expected"), [
    ("pl", "pl"), ("en", "en"), ("xx", "en"), ("", None), (None, None), ("auto", None),
])
def test_resolve_language(setting, expected):
    """None w oczekiwaniu = 'to, co da system, o ile obslugiwane, inaczej en'."""
    from i18n import LANGUAGES, resolve_language, system_language
    got = resolve_language(setting)
    if expected is None:
        sys_code = system_language()
        expected = sys_code if sys_code in LANGUAGES else "en"
    assert got == expected


def test_nieznany_klucz_nie_jest_pustym_napisem():
    """Literowka w kluczu ma byc WIDOCZNA (sam klucz), nie cichym pustym labelem."""
    assert t("no.such.key") == "no.such.key"


# --------------------------------------------------------------------------- #
# Session.stalled - sesja stoi z otwarta tura (regresja PID 26356, 2026-09-06)
# --------------------------------------------------------------------------- #
def test_stalled_true_for_long_open_turn_without_subagents() -> None:
    session = make_session(turn=TURN_OPEN, working=True, active_subagents=0,
                           silence=STALLED_TURN_SECONDS + 1,
                           turn_reason="turn.processing_tool_result")
    assert session.stalled is True


def test_stalled_false_just_below_threshold() -> None:
    session = make_session(turn=TURN_OPEN, working=True, active_subagents=0,
                           silence=STALLED_TURN_SECONDS - 1)
    assert session.stalled is False


def test_stalled_false_when_subagents_still_write() -> None:
    """Subagent pisze -> plik sesji stoi, ale praca TRWA. To nie jest zawieszenie."""
    session = make_session(turn=TURN_OPEN, working=True, active_subagents=3,
                           silence=STALLED_TURN_SECONDS * 10)
    assert session.stalled is False


def test_stalled_false_for_closed_turn() -> None:
    """Domknieta tura po godzinach ciszy to sesja SKONCZONA, nie zawieszona."""
    session = make_session(turn=TURN_CLOSED, working=False, silence=86400.0)
    assert session.stalled is False


def test_stalled_false_for_unknown_turn() -> None:
    """UNKNOWN blokuje wylaczenie, ale nie wolno go zglaszac jako 'czeka na Ciebie'."""
    session = make_session(turn=TURN_UNKNOWN, working=True, silence=86400.0)
    assert session.stalled is False


def test_stalled_does_not_change_the_shutdown_gate() -> None:
    """Kluczowa asercja: ostrzezenie jest DODATKIEM, nie osłabieniem bramki.

    Sesja stojaca 10 h z otwarta tura ma dalej wychodzic jako pracujaca - dokladnie
    tak, jak zachowal sie program przy PID 26356.
    """
    assert is_working(turn=TURN_OPEN, silence=36000.0, quiet_seconds=300.0,
                      active_subagents=0) is True


def test_stalled_session_why_shows_duration() -> None:
    session = make_session(turn=TURN_OPEN, working=True,
                           silence=STALLED_TURN_SECONDS + 60,
                           turn_reason="turn.processing_tool_result")
    assert fmt_duration(session.silence) in session.why
