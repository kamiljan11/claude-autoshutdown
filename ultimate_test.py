"""Test koncowy: pelny lancuch na SZTUCZNYM srodowisku, bez ryzyka dla prawdziwej maszyny.

Buduje tymczasowy katalog ~/.claude z prawdziwymi plikami sesji i transkryptami,
uruchamia prawdziwy proces o nazwie claude.exe (kopia interpretera), i sprawdza czy
program przechodzi ze stanu CZEKAJ do WYLACZ dokladnie wtedy, kiedy powinien.

Etapy:
  1. sesja z otwarta tura (tool_use)          -> musi CZEKAC
  2. sesja w trakcie kompaktowania kontekstu  -> musi CZEKAC (mtime stoi!)
  3. dwie sesje, jedna pracuje                -> musi CZEKAC
  4. wszystkie tury domkniete, dluga cisza    -> musi WYLACZYC
  5. proces sesji ubity, plik sesji zostaje   -> smiec musi byc odrzucony
  6. GUI end-to-end: uzbrojone, tryb bojowy, akcja 'nothing' -> log ma pokazac
     pelen lancuch az do wykonania akcji

Uruchom: python ultimate_test.py
Wynik ladu je w ultimate_test_report.log obok programu.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from monitor import SessionScanner, evaluate, fmt_duration
from winprobe import probe_process

APP_DIR = Path(__file__).resolve().parent
REPORT = APP_DIR / "ultimate_test_report.log"
PROJECT_SLUG = "C--test-projekt"

report_lines: list[str] = []


def say(text: str = "") -> None:
    stamp = datetime.now(UTC).astimezone().strftime("%H:%M:%S")
    line = f"[{stamp}] {text}" if text else ""
    print(line)
    report_lines.append(line)


class FakeEnvironment:
    """Tymczasowy ~/.claude + zywy proces udajacy sesje."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="autoshutdown-test-"))
        self.claude = self.root / "claude-home"
        (self.claude / "sessions").mkdir(parents=True)
        (self.claude / "projects" / PROJECT_SLUG).mkdir(parents=True)
        self.procs: list[subprocess.Popen] = []
        # Skaner wymaga, zeby nazwa exe zawierala "claude" - robimy kopie interpretera.
        self.fake_exe = self.root / "claude.exe"
        shutil.copy2(sys.executable, self.fake_exe)

    def spawn_session(self, name: str, session_id: str) -> int:
        proc = subprocess.Popen(
            [str(self.fake_exe), "-c", "import time; time.sleep(900)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.procs.append(proc)
        for _ in range(50):  # czekamy az system zaraportuje czas startu
            info = probe_process(proc.pid)
            if info.alive and info.created_filetime:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("proces testowy nie wystartowal")

        meta = {
            "pid": proc.pid,
            "sessionId": session_id,
            "cwd": r"C:\test\projekt",
            "startedAt": int(time.time() * 1000) - 3_600_000,
            "procStart": str(info.created_filetime),
            "version": "test",
            "kind": "interactive",
            "entrypoint": "claude-desktop",
            "name": name,
        }
        (self.claude / "sessions" / f"{proc.pid}.json").write_text(
            json.dumps(meta), encoding="utf-8")
        return proc.pid

    def transcript(self, session_id: str) -> Path:
        return self.claude / "projects" / PROJECT_SLUG / f"{session_id}.jsonl"

    def write_transcript(self, session_id: str, records: list[dict],
                         age_seconds: float = 0.0) -> None:
        path = self.transcript(session_id)
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(path, (old, old))

    def kill(self, pid: int) -> None:
        for proc in self.procs:
            if proc.pid == pid:
                proc.kill()
                proc.wait(timeout=10)

    def cleanup(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
        for proc in self.procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(self.root, ignore_errors=True)


ASSISTANT_TOOL = {"type": "assistant", "message": {"stop_reason": "tool_use",
                                                   "content": [{"type": "tool_use",
                                                                "name": "Bash"}]}}
ASSISTANT_DONE = {"type": "assistant", "message": {"stop_reason": "end_turn",
                                                   "content": [{"type": "text",
                                                                "text": "gotowe"}]}}
USER_TOOL_RESULT = {"type": "user", "message": {"content": [{"type": "tool_result"}]}}
COMPACT = {"type": "user", "isCompactSummary": True,
           "message": {"content": "streszczenie rozmowy"}}
NOISE = {"type": "attachment"}

BASE = {
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
    "required_polls": 1,
}


def check(scanner: SessionScanner, expect_shutdown: bool, label: str, **extra) -> bool:
    sessions = scanner.scan(quiet_seconds=300.0)
    args = {**BASE, **extra}
    if "scan_error" not in args:
        args["scan_error"] = scanner.last_error
    verdict = evaluate(sessions, stable_polls=0, **args)
    got = verdict.ok
    ok = got == expect_shutdown
    say(f"  {'ZDANY ' if ok else 'OBLANY'} {label}")
    say(f"          sesji: {len(sessions)}  werdykt: "
        f"{'WYLACZ' if got else 'CZEKAJ'}  (oczekiwano: "
        f"{'WYLACZ' if expect_shutdown else 'CZEKAJ'})")
    for session in sessions:
        say(f"          - {session.name:14} {session.state:10} "
            f"cisza {fmt_duration(session.silence):8} tura={session.turn:8} {session.why}")
    if not got:
        for blocker in verdict.blockers:
            say(f"          blokuje: {blocker[:110]}")
    return ok


def headless_chain(env: FakeEnvironment) -> list[bool]:
    scanner = SessionScanner(root=env.claude)
    results: list[bool] = []

    say("ETAP 1: jedna sesja, otwarta tura (narzedzie w toku)")
    sid_a = "aaaaaaaa-0000-0000-0000-00000000000a"
    pid_a = env.spawn_session("test-pracuje", sid_a)
    env.write_transcript(sid_a, [ASSISTANT_DONE, USER_TOOL_RESULT, ASSISTANT_TOOL],
                         age_seconds=1200)
    results.append(check(scanner, expect_shutdown=False,
                         label="otwarta tura blokuje mimo 20 min ciszy"))
    say()

    say("ETAP 2: ta sama sesja w trakcie kompaktowania kontekstu")
    env.write_transcript(sid_a, [ASSISTANT_DONE, COMPACT], age_seconds=2400)
    results.append(check(scanner, expect_shutdown=False,
                         label="kompaktowanie blokuje mimo 40 min ciszy"))
    say()

    say("ETAP 3: druga sesja dochodzi i pracuje, pierwsza skonczyla")
    env.write_transcript(sid_a, [ASSISTANT_TOOL, USER_TOOL_RESULT, ASSISTANT_DONE, NOISE],
                         age_seconds=1800)
    sid_b = "bbbbbbbb-0000-0000-0000-00000000000b"
    pid_b = env.spawn_session("test-druga", sid_b)
    env.write_transcript(sid_b, [ASSISTANT_TOOL], age_seconds=900)
    results.append(check(scanner, expect_shutdown=False,
                         label="jedna pracujaca sesja wystarczy, zeby czekac"))
    say()

    say("ETAP 4: obie tury domkniete, cisza dluzsza niz prog")
    env.write_transcript(sid_b, [ASSISTANT_TOOL, USER_TOOL_RESULT, ASSISTANT_DONE],
                         age_seconds=1500)
    results.append(check(scanner, expect_shutdown=True,
                         label="wszystko skonczone -> wolno wylaczyc"))
    say()

    say("ETAP 5: proces sesji ubity, plik sesji zostaje jako smiec")
    env.kill(pid_b)
    time.sleep(1.0)
    sessions = scanner.scan(quiet_seconds=300.0)
    stale_gone = all(s.pid != pid_b for s in sessions)
    say(f"  {'ZDANY ' if stale_gone else 'OBLANY'} martwy PID {pid_b} odrzucony "
        f"(widoczne sesje: {[s.pid for s in sessions]})")
    results.append(stale_gone)
    say()

    say(f"ETAP 5b: kontrola - zywa sesja {pid_a} nadal widoczna")
    alive_ok = any(s.pid == pid_a for s in sessions)
    say(f"  {'ZDANY ' if alive_ok else 'OBLANY'} zywa sesja nie zniknela")
    results.append(alive_ok)
    say()

    say("ETAP 6: rekord wyniku narzedzia WIEKSZY niz okno odczytu transkryptu")
    say("        (w zywych transkryptach 494 takich rekordow, najwiekszy 5,1 MB)")
    huge = {"type": "user", "message": {"content": [{"type": "tool_result",
                                                     "text": "x" * 400_000}]}}
    env.write_transcript(sid_a, [ASSISTANT_DONE, huge], age_seconds=1800)
    results.append(check(scanner, expect_shutdown=False,
                         label="gigantyczny rekord nie moze dac 'nie wiem' = 'skonczone'"))
    say()

    say("ETAP 7: nieznany typ rekordu po otwartej turze")
    env.write_transcript(sid_a, [ASSISTANT_TOOL, {"type": "typ-z-przyszlosci"}],
                         age_seconds=1800)
    results.append(check(scanner, expect_shutdown=False,
                         label="nowy typ rekordu nie kasuje otwartej tury"))
    say()

    say("ETAP 8: awaria skanera (znika katalog sessions)")
    env.write_transcript(sid_a, [ASSISTANT_TOOL, USER_TOOL_RESULT, ASSISTANT_DONE],
                         age_seconds=1800)
    results.append(check(scanner, expect_shutdown=False,
                         label="blad skanera blokuje, zamiast udawac 'zero sesji'",
                         scan_error="brak katalogu sessions"))
    say()

    say("ETAP 9: proces sesji zyje, ale nie ma go w rejestrze")
    results.append(check(scanner, expect_shutdown=False,
                         label="niewidzialna sesja blokuje wylaczenie",
                         unregistered_pids=[99999]))
    say()

    say("ETAP 10: wszystkie sesje zniknely przed chwila")
    results.append(_check_no_sessions(expect=False, since=5.0))
    say()

    say("ETAP 11: sesje zniknely dawno")
    results.append(_check_no_sessions(expect=True, since=999.0))
    return results


def _check_no_sessions(expect: bool, since: float) -> bool:
    """Werdykt dla pustej listy sesji przy zadanym czasie od ostatniej."""
    verdict = evaluate([], stable_polls=0, **{**BASE, "scan_error": "",
                                              "seconds_since_last_session": since})
    ok = verdict.ok == expect
    say(f"  {'ZDANY ' if ok else 'OBLANY'} zero sesji, ostatnia {fmt_duration(since)} temu "
        f"-> {'WYLACZ' if verdict.ok else 'CZEKAJ'} (oczekiwano "
        f"{'WYLACZ' if expect else 'CZEKAJ'})")
    if not verdict.ok:
        for blocker in verdict.blockers:
            say(f"          blokuje: {blocker[:110]}")
    return ok


def gui_chain(env: FakeEnvironment) -> bool:
    """Uruchamia PRAWDZIWE GUI w trybie bojowym z akcja 'nothing'."""
    home = env.root / "app-home"
    home.mkdir(exist_ok=True)
    config = {
        "poll_seconds": 2, "quiet_seconds": 60, "required_polls": 2,
        "countdown_seconds": 3, "action": "nothing",
        "dry_run": False,                 # TRYB BOJOWY - akcja 'nothing' jest bezpieczna
        "require_human_idle": False,      # test jedzie bez czlowieka przy klawiaturze
        "human_idle_required": 600, "allow_zero_sessions": False, "guard_patterns": [],
        "language": "en",           # grepy nizej sa po angielsku, niezaleznie od systemu
    }
    (home / "config.json").write_text(json.dumps(config), encoding="utf-8")

    sid = "cccccccc-0000-0000-0000-00000000000c"
    env.spawn_session("test-gui", sid)
    env.write_transcript(sid, [ASSISTANT_TOOL], age_seconds=0)  # najpierw PRACUJE

    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(APP_DIR / "autoshutdown.py")],
        env={**os.environ,
             "CLAUDE_CONFIG_DIR": str(env.claude),
             "CLAUDE_AUTOSHUTDOWN_HOME": str(home),
             "CLAUDE_AUTOSHUTDOWN_AUTOARM": "1"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        say("  GUI wystartowalo, sesja PRACUJE - czekam 8 s, akcja NIE moze odpalic")
        time.sleep(8)
        gui_log = (home / "autoshutdown.log").read_text(encoding="utf-8")
        if "countdown" in gui_log or "ACTION" in gui_log:
            say("  OBLANY GUI zaczelo odliczac mimo pracujacej sesji")
            return False
        say("  ZDANY  brak odliczania przy pracujacej sesji")

        say("  domykam ture i cofam mtime o 5 min - teraz akcja MUSI odpalic")
        env.write_transcript(sid, [ASSISTANT_TOOL, USER_TOOL_RESULT, ASSISTANT_DONE],
                             age_seconds=300)
        deadline = time.time() + 45
        while time.time() < deadline:
            gui_log = (home / "autoshutdown.log").read_text(encoding="utf-8")
            if "ACTION EXECUTED" in gui_log:
                say("  ZDANY  pelny lancuch przeszedl do wykonania akcji")
                for line in gui_log.splitlines():
                    say(f"          | {line}")
                return True
            time.sleep(1)
        say("  OBLANY akcja nie odpalila w 45 s")
        for line in gui_log.splitlines():
            say(f"          | {line}")
        return False
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    say("=" * 78)
    say("TEST KONCOWY Claude AutoShutdown")
    say(f"Python {sys.version.split()[0]} | {datetime.now(UTC).astimezone():%Y-%m-%d %H:%M:%S}")
    say("=" * 78)
    say()

    env = FakeEnvironment()
    say(f"Sztuczne srodowisko: {env.claude}")
    say()
    try:
        results = headless_chain(env)
        say("=" * 78)
        say("ETAP 12: GUI end-to-end (tryb bojowy, akcja 'nothing')")
        results.append(gui_chain(env))
    finally:
        env.cleanup()

    passed = sum(1 for r in results if r)
    say()
    say("=" * 78)
    say(f"WYNIK: {passed}/{len(results)} etapow zdanych")
    say("=" * 78)
    REPORT.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"\nRaport: {REPORT}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
