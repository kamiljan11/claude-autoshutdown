"""Silnik: skanuje zywe sesje Claude Code / Cowork i decyduje o wylaczeniu.

Zrodlo prawdy (zweryfikowane na tej maszynie, nie zgadywane):
  ~/.claude/sessions/<PID>.json               - rejestr sesji: pid, sessionId, cwd,
                                                procStart (FILETIME), entrypoint, name
  ~/.claude/projects/<slug>/<sessionId>.jsonl - transkrypt; mtime rosnie przy kazdym
                                                zapisie modelu/narzedzia
  ~/.claude/projects/<slug>/<sessionId>/subagents/*.jsonl - rownolegli subagenci

Po pliku sesji zostaje smiec gdy proces zginie, dlatego kazdy wpis jest
weryfikowany dwuetapowo: PID musi zyc ORAZ czas startu procesu musi zgadzac sie
z procStart (inaczej to inny proces, ktory dostal ten sam PID).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from i18n import t
from winprobe import FILETIME_PER_SECOND, ProcInfo, probe_process

# Tolerancja porownania czasu startu procesu (1 s) - chroni przed recyklingiem PID.
PROC_START_TOLERANCE = FILETIME_PER_SECOND
SUBAGENT_ACTIVE_WINDOW = 120.0  # subagent "swiezy" gdy pisal w ostatnich 2 min
SUBAGENT_LIST_LIMIT = 20        # ile subagentow pokazujemy w Podgladzie per sesja
# Ponizej tego odstepu pomiar %CPU jest smieciem (dzielenie przez mala liczbe daje
# dziesiatki procent dla spiacego procesu -> sesja na zawsze "PRACUJE").
MIN_CPU_SAMPLE_SECONDS = 1.0

# Otwarta tura bez ANI JEDNEGO zapisu przez tyle sekund = sesja stoi. Zmierzone
# 2026-09-06 na sesji PID 26356: tura otwarta na `tool_use`, po czym 10 h 03 min
# ciszy, a wynik narzedzia i realny zapis pliku dopiero po powrocie czlowieka -
# sesja czekala na zgode w oknie uprawnien. To NIE jest powod, zeby wylaczyc
# komputer (praca byla w polowie), ale JEST powod, zeby to krzyknac na ekranie.
STALLED_TURN_SECONDS = 1800.0

# O stanie tury decyduja WYLACZNIE rekordy rozmowy. Cala reszta to szum techniczny.
#
# Swiadomie jest to lista DOZWOLONYCH, nie lista szumu: w zywych transkryptach na tej
# maszynie siedza tez frame-link, pr-link, artifact-comment-monitor, permission-mode
# i inne, ktore dopisano po napisaniu tego programu. Przy liscie szumu kazdy nowy typ
# konczylby jako "nie wiem" i kasowal wykryta otwarta ture.
DECISIVE_RECORD_TYPES = frozenset({"assistant", "user"})

# Stany tury.
TURN_CLOSED = "CLOSED"      # model odpowiedzial i czeka na czlowieka
TURN_OPEN = "OPEN"          # cos jeszcze trwa (narzedzie, myslenie, kompaktowanie)
TURN_UNKNOWN = "UNKNOWN"    # brak transkryptu / nie da sie odczytac


def claude_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".claude"


# --------------------------------------------------------------------------- #
# Model danych
# --------------------------------------------------------------------------- #
@dataclass
class Session:
    pid: int
    session_id: str
    name: str
    cwd: str
    entrypoint: str          # claude-desktop (Cowork / aplikacja) albo cli (terminal)
    kind: str
    started_at: float        # epoch s
    transcript: Path | None
    last_activity: float     # epoch s, max(mtime transkryptu, mtime subagentow)
    silence: float           # sekundy od last_activity
    cpu_percent: float       # zmierzone miedzy dwoma odczytami
    active_subagents: int
    working: bool
    turn: str = TURN_UNKNOWN  # CLOSED / OPEN / UNKNOWN
    turn_reason: str = ""     # KLUCZ i18n (t(turn_reason, **turn_params) daje tekst)
    turn_params: dict = field(default_factory=dict)
    subagent_files: list[tuple[Path, float]] = field(default_factory=list)  # (sciezka, mtime)

    @property
    def turn_text(self) -> str:
        return t(self.turn_reason, **self.turn_params) if self.turn_reason else ""

    @property
    def state(self) -> str:
        return t("state.working") if self.working else t("state.idle")

    @property
    def why(self) -> str:
        """Powod stanu - to trafia do kolumny w GUI."""
        if not self.working:
            return self.turn_text or t("why.silence")
        if self.turn == TURN_OPEN:
            # Przerwana tura (np. Esc w trakcie narzedzia) zostaje OPEN na zawsze
            # i blokuje wylaczenie. Nie zgadujemy za uzytkownika - pokazujemy,
            # jak dlugo to trwa, zeby sam zobaczyl porzucona sesje.
            if self.stalled:
                return t("why.blocking_since", reason=self.turn_text,
                         duration=fmt_duration(self.silence))
            return self.turn_text
        if self.active_subagents:
            return t("why.subagents_writing", n=self.active_subagents)
        if self.turn == TURN_UNKNOWN:
            return t("why.unknown_turn", reason=self.turn_text)
        return t("why.fresh_write")

    @property
    def stalled(self) -> bool:
        """Tura otwarta, subagenci milcza, plik nie rosnie od pol godziny.

        Celowo NIE wplywa na `is_working` ani na werdykt - sesja w tym stanie
        dalej blokuje wylaczenie, bo jej praca jest przerwana w polowie. Sluzy
        wylacznie do tego, zeby czlowiek zajrzal do sesji.

        DWUZNACZNOSC, ktorej NIE DA SIE tu rozstrzygnac: pytanie o uprawnienia
        czekajace na czlowieka, jedno narzedzie dzialajace ponad pol godziny
        (build, deploy) i czekanie na limit API zapisuja w transkrypcie ten sam
        rekord - `assistant` ze `stop_reason: tool_use`, czyli
        `turn.tool_in_flight`. Rozroznienia nie ma tez w CPU (pomiar 2026-09-02:
        rozklady sesji bezczynnej i pracujacej sie pokrywaja). Dlatego komunikat
        w GUI i w logu podaje POMIAR ("nic nie zapisala od X") i wymienia
        mozliwe przyczyny, zamiast twierdzic ktorakolwiek z nich.
        """
        return (self.turn == TURN_OPEN
                and self.active_subagents == 0
                and self.silence >= STALLED_TURN_SECONDS)

    @property
    def short_cwd(self) -> str:
        return os.path.basename(self.cwd.rstrip("\\/")) or self.cwd

    @property
    def surface(self) -> str:
        return t("surface.cowork") if self.entrypoint == "claude-desktop" else t("surface.cli")


@dataclass
class Verdict:
    """Wynik jednego cyklu: czy wolno wylaczyc i co blokuje."""
    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    stable_polls: int = 0
    required_polls: int = 0

    @property
    def blockers(self) -> list[str]:
        return [f"{name}: {detail}" for name, passed, detail in self.checks if not passed]


def is_working(
    *,
    turn: str,
    silence: float,
    quiet_seconds: float,
    active_subagents: int,
) -> bool:
    """Czy sesja wciaz pracuje. Jedno miejsce z ta decyzja - reszta tylko pyta.

    Otwarta tura bije cisze: podczas /compact albo czekania na limit API plik
    transkryptu stoi w miejscu, a sesja NIE skonczyla pracy.

    CPU NIE jest kryterium i celowo nie ma go w sygnaturze. Pomiar na zywej maszynie
    (6 probek po 5 s, 2026-09-02): sesje z domknieta tura chodzily na 0,9-2,0 % ze
    szczytami do 5,0 %, a sesja realnie pracujaca na 2,7 % ze szczytem 5,3 %. Rozklady
    sie pokrywaja, wiec CPU nie niesie informacji. Zostaje jako kolumna w GUI - do
    patrzenia, nie do decydowania.
    """
    if turn != TURN_CLOSED:
        # OPEN = praca trwa. UNKNOWN = nie wiemy, a niewiedza NIE jest zgoda na
        # wylaczenie: awaria odczytu, plik zablokowany przez zapis, nieznany format
        # rekordu - kazde z tego wygladalo wczesniej jak "sesja skonczyla".
        return True
    if active_subagents > 0:
        return True
    return silence < quiet_seconds


# --------------------------------------------------------------------------- #
# Skaner
# --------------------------------------------------------------------------- #
class SessionScanner:
    """Trzyma stan miedzy odczytami (potrzebny do policzenia %CPU)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or claude_dir()
        self.sessions_dir = self.root / "sessions"
        self.projects_dir = self.root / "projects"
        self._transcript_cache: dict[str, Path] = {}
        self._cpu_prev: dict[int, tuple[int, float]] = {}
        self._cpu_last: dict[int, float] = {}
        self._turn_cache: dict[str, tuple[float, str, str, dict]] = {}
        self.last_error: str = ""
        # Kontrola krzyzowa z lista procesow systemu ma sens tylko wtedy, gdy
        # czytamy PRAWDZIWY rejestr uzytkownika. Przy podmienionym katalogu (test,
        # druga instalacja) kazda zywa sesja wygladalaby jak "proces bez wpisu".
        # Porownujemy z ~/.claude wprost, a nie z claude_dir(): ta druga czyta te
        # sama zmienna srodowiskowa, wiec porownanie zawsze wychodziloby prawdziwe.
        self.is_default_root = self.root == Path(os.path.expanduser("~")) / ".claude"

    def find_transcript(self, session_id: str) -> Path | None:
        cached = self._transcript_cache.get(session_id)
        if cached and cached.exists():
            return cached
        if not session_id or not self.projects_dir.is_dir():
            return None
        for project in self.projects_dir.iterdir():
            candidate = project / f"{session_id}.jsonl"
            if candidate.exists():
                self._transcript_cache[session_id] = candidate
                return candidate
        return None

    @staticmethod
    def _subagent_activity(transcript: Path | None,
                           active_window: float = SUBAGENT_ACTIVE_WINDOW,
                           ) -> tuple[float, int]:
        """Zwraca (najswiezszy mtime subagenta, liczba swiezych subagentow)."""
        newest, active = 0.0, 0
        now = time.time()
        for _path, mtime in SessionScanner.subagent_transcripts(transcript):
            newest = max(newest, mtime)
            if now - mtime <= active_window:
                active += 1
        return newest, active

    @staticmethod
    def subagent_transcripts(transcript: Path | None,
                             limit: int = SUBAGENT_LIST_LIMIT) -> list[tuple[Path, float]]:
        """Transkrypty subagentow sesji jako (sciezka, mtime), najswiezsze pierwsze.

        Szukamy REKURENCYJNIE: zwykli subagenci leza wprost w `subagents/`, ale
        agenci uruchomieni przez Workflow siedza w `subagents/workflows/wf_*/`.
        Plaskie przeszukanie ich nie widzialo - a to wlasnie one potrafia mielic
        godzinami po tym, jak glowna sesja juz zamilkla. Limit chroni Podglad
        przed lista 150 agentow z jednego workflow.
        """
        if transcript is None:
            return []
        sub_dir = transcript.with_suffix("") / "subagents"
        if not sub_dir.is_dir():
            return []
        found: list[tuple[Path, float]] = []
        for f in sub_dir.rglob("*.jsonl"):
            if f.name == "journal.jsonl":
                continue  # dziennik workflow, nie rozmowa agenta
            try:
                found.append((f, f.stat().st_mtime))
            except OSError:
                continue
        found.sort(key=lambda item: item[1], reverse=True)
        return found[:limit]

    def _turn_state_cached(self, transcript: Path | None, mtime: float,
                           ) -> tuple[str, str, dict]:
        """Ogon transkryptu czytamy tylko gdy plik sie zmienil (bywa >200 MB)."""
        if transcript is None:
            return TURN_UNKNOWN, "turn.no_transcript", {}
        key = str(transcript)
        cached = self._turn_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1], cached[2], cached[3]
        state, reason, params = turn_state(transcript)
        self._turn_cache[key] = (mtime, state, reason, params)
        return state, reason, params

    def _cpu_percent(self, proc: ProcInfo) -> float:
        """Srednie zuzycie CPU miedzy dwoma odczytami.

        Zbyt krotki odstep odrzucamy: kilkaset ms startu procesu podzielone przez
        20 ms daje kilkadziesiat procent dla procesu, ktory tylko spi. Taki odczyt
        zamrazalby sesje w stanie PRACUJE i komputer nigdy by sie nie wylaczyl.
        """
        now = time.monotonic()
        prev = self._cpu_prev.get(proc.pid)
        if prev is None:
            self._cpu_prev[proc.pid] = (proc.cpu_100ns, now)
            return 0.0
        prev_cpu, prev_t = prev
        elapsed = now - prev_t
        if elapsed < MIN_CPU_SAMPLE_SECONDS:
            # Nie nadpisujemy punktu odniesienia - kolejny odczyt dostanie sensowny odstep.
            return self._cpu_last.get(proc.pid, 0.0)
        self._cpu_prev[proc.pid] = (proc.cpu_100ns, now)
        used_s = (proc.cpu_100ns - prev_cpu) / FILETIME_PER_SECOND
        value = max(0.0, min(100.0 * used_s / elapsed, 100.0))
        self._cpu_last[proc.pid] = value
        return value

    def _is_live_claude(self, meta: dict, proc: ProcInfo) -> bool:
        """Czy wpis opisuje NAPRAWDE ten proces (a nie martwy plik / obcy PID)."""
        if not proc.alive or "claude" not in proc.name.lower():
            return False
        declared = meta.get("procStart")
        if not declared:
            return True  # starszy format bez procStart - polegamy na nazwie exe
        try:
            return abs(int(declared) - proc.created_filetime) <= PROC_START_TOLERANCE
        except (TypeError, ValueError):
            return True

    def scan(self, quiet_seconds: float) -> list[Session]:
        self.last_error = ""
        if not self.sessions_dir.is_dir():
            self.last_error = f"brak katalogu {self.sessions_dir}"
            return []

        now = time.time()
        found: list[Session] = []
        live_pids: set[int] = set()

        for meta_file in sorted(self.sessions_dir.glob("*.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except FileNotFoundError:
                # Plik zniknal miedzy listowaniem katalogu a odczytem. To NORMALNE
                # zamkniecie sesji Claude, nie awaria skanera - zgloszone jako blad
                # zerowaloby licznik potwierdzen przy kazdym zamknietym oknie.
                continue
            except (OSError, ValueError) as exc:
                # ValueError obejmuje JSONDecodeError ORAZ UnicodeDecodeError:
                # ten drugi nie jest OSError i wczesniej uciekal z handlera,
                # wywracajac caly cykl skanowania przez jeden zly plik.
                self.last_error = f"{meta_file.name}: {exc}"
                continue
            if not isinstance(meta, dict):
                self.last_error = f"{meta_file.name}: nie jest obiektem JSON"
                continue

            pid = meta.get("pid")
            if not isinstance(pid, int):
                continue
            proc = probe_process(pid)
            if not self._is_live_claude(meta, proc):
                continue
            live_pids.add(pid)

            session_id = meta.get("sessionId", "")
            transcript = self.find_transcript(session_id)
            transcript_mtime = 0.0
            if transcript is not None:
                try:
                    transcript_mtime = transcript.stat().st_mtime
                except OSError:
                    transcript_mtime = 0.0
            # Okno nie moze byc krotsze niz prog ciszy: subagent czekajacy
            # na odpowiedz API milczy minutami, a nadal pracuje.
            sub_mtime, active_subs = self._subagent_activity(
                transcript, max(SUBAGENT_ACTIVE_WINDOW, quiet_seconds))

            started_at = meta.get("startedAt", 0) / 1000.0
            last_activity = max(transcript_mtime, sub_mtime, started_at)
            if last_activity > now + 60:
                # mtime z przyszlosci (skok zegara, zly czas pliku) dawalby cisze 0
                # na zawsze - program nigdy by nie wylaczyl i nie mowilby dlaczego.
                self.last_error = t("scan.future_mtime", name=meta.get("name") or session_id[:8])
            silence = max(0.0, now - last_activity) if last_activity else float("inf")
            cpu = self._cpu_percent(proc)
            turn, turn_reason, turn_params = self._turn_state_cached(
                transcript, transcript_mtime)
            working = is_working(
                turn=turn, silence=silence, quiet_seconds=quiet_seconds,
                active_subagents=active_subs,
            )

            found.append(Session(
                pid=pid,
                session_id=session_id,
                name=meta.get("name") or session_id[:8],
                cwd=meta.get("cwd", ""),
                entrypoint=meta.get("entrypoint", ""),
                kind=meta.get("kind", ""),
                started_at=started_at,
                transcript=transcript,
                last_activity=last_activity,
                silence=silence,
                cpu_percent=cpu,
                active_subagents=active_subs,
                working=working,
                turn=turn,
                turn_reason=turn_reason,
                turn_params=turn_params,
                subagent_files=self.subagent_transcripts(transcript),
            ))

        # Program chodzi calymi dobami, a sesji przybywa i ubywa. Bez sprzatania
        # slowniki rosna w nieskonczonosc dla dawno zamknietych sesji.
        for pid in list(self._cpu_prev):
            if pid not in live_pids:
                self._cpu_prev.pop(pid, None)
                self._cpu_last.pop(pid, None)
        live_ids = {s.session_id for s in found}
        for session_id in list(self._transcript_cache):
            if session_id not in live_ids:
                self._transcript_cache.pop(session_id, None)
        live_transcripts = {str(s.transcript) for s in found if s.transcript}
        for key in list(self._turn_cache):
            if key not in live_transcripts:
                self._turn_cache.pop(key, None)

        found.sort(key=lambda s: (not s.working, s.name))
        return found


# --------------------------------------------------------------------------- #
# Silnik decyzyjny (czysta funkcja - latwa do przetestowania)
# --------------------------------------------------------------------------- #
def evaluate(
    sessions: list[Session],
    *,
    quiet_seconds: float,
    armed: bool,
    stop_file_present: bool,
    human_idle: float,
    require_human_idle: bool,
    human_idle_required: float,
    allow_zero_sessions: bool,
    saw_any_session: bool,
    guard_patterns: list[str],
    guard_hits: list[str] | None,
    stable_polls: int,
    required_polls: int,
    scan_error: str = "",
    seconds_since_last_session: float = 0.0,
    unregistered_pids: list[int] | None = None,
) -> Verdict:
    """Zbiera wszystkie warunki. Akcja tylko gdy KAZDY jest zdany."""
    checks: list[tuple[str, bool, str]] = []

    checks.append((t("check.armed"), armed,
                   t("check.armed.yes") if armed else t("check.armed.no")))
    checks.append((t("check.no_stop_file"), not stop_file_present,
                   t("check.no_stop_file.blocked") if stop_file_present
                   else t("check.no_stop_file.ok")))
    # Awaria skanera daje pusta liste sesji, a pusta lista przechodzila wszystkie
    # ponizsze warunki. "Nic nie widze" nie moze znaczyc "nic nie pracuje".
    checks.append((t("check.scanner_ok"), not scan_error,
                   scan_error or t("check.scanner_ok.ok")))
    # Kontrola krzyzowa z systemem: proces sesji, ktorego nie ma w rejestrze,
    # bylby dla monitora niewidzialny. Lepiej zablokowac niz zgadywac.
    stray = unregistered_pids or []
    checks.append((
        t("check.registry"),
        not stray,
        t("check.registry.stray", pids=stray) if stray else t("check.registry.ok"),
    ))

    working = [s for s in sessions if s.working]
    checks.append((
        t("check.all_idle"),
        not working,
        "; ".join(f"{s.name} [{s.short_cwd}] - {s.why}" for s in working) if working
        else t("check.all_idle.ok", n=len(sessions)),
    ))

    # Nie tylko OPEN: UNKNOWN tez blokuje. Wczesniej sesja z nieczytelnym ogonem
    # transkryptu przechodzila ten warunek i program raportowal "wszystkie tury
    # domkniete", chociaz nie wiedzial o niej nic.
    unclosed = [s for s in sessions if s.turn != TURN_CLOSED]
    checks.append((
        t("check.turns_closed"),
        not unclosed,
        "; ".join(f"{s.name}: {s.turn_text}" for s in unclosed) if unclosed
        else t("check.turns_closed.ok", n=len(sessions)),
    ))

    if sessions:
        min_silence = min(s.silence for s in sessions)
        checks.append((
            t("check.quiet_each", s=int(quiet_seconds)),
            min_silence >= quiet_seconds,
            t("check.quiet_each.detail", d=fmt_duration(min_silence)),
        ))
    else:
        # Zero sesji nie moze byc szybsza sciezka do wylaczenia niz sesja bezczynna.
        # Bez tego zamkniecie ostatniego okna Claude gasilo komputer po 30 s, zanim
        # czlowiek zdazylby zauwazyc, ze zamknal je przez pomylke albo ze cos padlo.
        quiet_enough = seconds_since_last_session >= quiet_seconds
        checks.append((
            t("check.quiet_since_last", s=int(quiet_seconds)),
            bool(allow_zero_sessions or (saw_any_session and quiet_enough)),
            t("check.quiet_since_last.none") if not saw_any_session
            else t("check.quiet_since_last.gone",
                   d=fmt_duration(seconds_since_last_session)),
        ))

    if require_human_idle:
        idle_ok = human_idle < 0 or human_idle >= human_idle_required
        checks.append((
            t("check.human_idle", s=int(human_idle_required)),
            idle_ok,
            t("check.human_idle.unknown") if human_idle < 0
            else t("check.human_idle.detail", d=fmt_duration(human_idle)),
        ))

    if guard_patterns:
        # guard_hits=None = tasklist zawiodl. Awaria skanu straznikow NIE moze
        # wygladac jak "czysto" - wtedy zywy ffmpeg przepuszczalby wylaczenie.
        if guard_hits is None:
            checks.append((t("check.no_guards"), False, t("check.no_guards.scan_failed")))
        else:
            checks.append((
                t("check.no_guards"),
                not guard_hits,
                ", ".join(guard_hits) if guard_hits else t("check.no_guards.ok"),
            ))

    all_ok = all(passed for _, passed, _ in checks)
    new_stable = stable_polls + 1 if all_ok else 0
    checks.append((
        t("check.confirmed", n=required_polls),
        new_stable >= required_polls,
        f"{new_stable}/{required_polls}",
    ))

    return Verdict(
        ok=all_ok and new_stable >= required_polls,
        checks=checks,
        stable_polls=new_stable,
        required_polls=required_polls,
    )


def classify_turn(event: dict) -> tuple[str, str, dict]:
    """Ostatni znaczacy rekord transkryptu -> (stan tury, opis po ludzku).

    Semantyka zweryfikowana na zywych transkryptach tej maszyny:
      assistant + stop_reason=end_turn   -> model skonczyl, czeka na czlowieka
      assistant + stop_reason=tool_use   -> zazadal narzedzia, praca trwa
      user (tool_result albo prompt)     -> pilka po stronie modelu: liczy odpowiedz,
                                            kompaktuje kontekst albo czeka na limit
      isCompactSummary                   -> wlasnie zjechala kompaktacja, praca wraca
    """
    if event.get("isCompactSummary"):
        return TURN_OPEN, "turn.compacting", {}

    kind = event.get("type")
    message = event.get("message") or {}
    if kind == "assistant":
        stop = message.get("stop_reason")
        if stop == "tool_use":
            return TURN_OPEN, "turn.tool_in_flight", {}
        if stop in ("end_turn", "stop_sequence"):
            return TURN_CLOSED, "turn.waiting_for_you", {}
        if stop == "max_tokens":
            return TURN_OPEN, "turn.cut_at_token_limit", {}
        return TURN_OPEN, "turn.reply_in_progress", {"stop": stop}
    if kind == "user":
        content = message.get("content")
        is_tool_result = isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        return TURN_OPEN, ("turn.processing_tool_result" if is_tool_result
                           else "turn.model_thinking"), {}
    return TURN_UNKNOWN, "turn.unknown_record", {"kind": kind}


def _read_tail(path: Path, max_bytes: int) -> tuple[list[bytes], bool]:
    """Ostatnie max_bytes pliku jako linie. Drugi element: czy plik ma jeszcze wiecej."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        start = max(0, size - max_bytes)
        fh.seek(start)
        blob = fh.read()
    lines = blob.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]  # pierwsza linia jest ucieta w polowie rekordu
    return lines, start > 0


def turn_state(path: Path | None, max_bytes: int = 256 * 1024,
               limit_bytes: int = 16 * 1024 * 1024) -> tuple[str, str, dict]:
    """Czyta ogon transkryptu i mowi, czy tura jest domknieta.

    To jest odpowiedz na kompaktowanie i czekanie na limit API: mtime pliku wtedy
    stoi, ale tura jest OTWARTA, wiec sesji nie wolno uznac za skonczona.

    Okno jest ADAPTACYJNE. Pojedynczy rekord potrafi byc wiekszy niz caly ogon:
    w transkryptach na tej maszynie jest 494 rekordow ponad 256 KB, najwiekszy
    5,1 MB, i prawie wszystkie to `user` z wynikiem narzedzia - czyli dokladnie
    ten rekord, ktory oznacza "model wlasnie dostal wynik i liczy dalej". Przy
    stalym oknie ogon nie zawieralby ani jednej kompletnej linii i sesja w trakcie
    pracy zostalaby uznana za nieznana.
    """
    if path is None:
        return TURN_UNKNOWN, "turn.no_transcript", {}

    window = max_bytes
    while True:
        try:
            lines, truncated = _read_tail(path, window)
        except OSError as exc:
            return TURN_UNKNOWN, "turn.cannot_read", {"error": exc.strerror or str(exc)}

        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if event.get("isCompactSummary"):
                return classify_turn(event)
            if event.get("type") not in DECISIVE_RECORD_TYPES:
                continue  # szum techniczny - nie niesie informacji o turze
            return classify_turn(event)

        if not truncated or window >= limit_bytes:
            return TURN_UNKNOWN, "turn.no_conversation_record", {}
        window *= 4  # rekord nie zmiescil sie w oknie - siegamy glebiej


def claude_processes_without_registry(known_pids: set[int]) -> list[int]:
    """PID-y zywych procesow Claude Code, ktorych NIE ma w rejestrze sesji.

    Rejestr `~/.claude/sessions/*.json` jest jedynym zrodlem listy sesji. Gdyby
    sesja go nie zapisala (inna wersja, inny entrypoint, blad zapisu), pracowalaby
    niewidzialna dla monitora. Tu patrzymy na system operacyjny zamiast wierzyc
    plikom - rozbieznosc lepiej zglosic niz zignorowac.
    """
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["tasklist", "/fi", "IMAGENAME eq claude.exe", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=15, check=False,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[int] = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2 or not parts[0].lower().startswith("claude"):
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        if pid in known_pids:
            continue
        # Sama nazwa claude.exe nie wystarczy: aplikacja desktopowa uruchamia
        # kilkanascie procesow pomocniczych (renderer, gpu, crashpad) o tej samej
        # nazwie. Sesja Claude Code to binarka spod claude-code\<wersja>\ i tylko
        # jej brak w rejestrze jest podejrzany. Zmierzone: 26 procesow claude.exe,
        # z czego sesji 6 - bez tego filtra kazdy cykl krzyczalby falszywym alarmem.
        exe = probe_process(pid).exe.lower().replace("/", "\\")
        if "claude-code\\" in exe:
            found.append(pid)
    return sorted(found)


def fmt_duration(seconds: float) -> str:
    if seconds == float("inf"):
        return "-"
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def matching_guard_processes(patterns: list[str]) -> list[str] | None:
    """Nazwy procesow pasujacych do wzorcow uzytkownika (regex albo podciag).

    None = nie udalo sie odczytac listy procesow. Rozroznienie jest celowe:
    pusta lista znaczy "sprawdzilem, czysto", None znaczy "nie wiem" - i to
    drugie ma blokowac wylaczenie, nie przepuszczac.
    """
    if not patterns:
        return []
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=15, check=False,
            encoding="utf-8", errors="replace",  # akcentowana nazwa procesu != crash
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = {line.split('","')[0].lstrip('"').lower()
             for line in out.splitlines() if line.strip()}
    if not names:
        return None  # tasklist odpowiedzial pustka - to awaria, nie "czysto"
    return sorted({name for name in names
                   if any(_pattern_matches(p, name) for p in patterns)})


def _pattern_matches(pattern: str, name: str) -> bool:
    r"""Dopasowanie wzorca straznika do nazwy procesu.

    Uzytkownik pisze naturalnie: `ffmpeg`, `ffmpeg*`, `.*mpeg\.exe`. Sam regex nie
    wystarcza - `ffmpeg*` jest legalnym regexem (zero lub wiecej `g`), wiec nie
    rzuca bledu, tylko po cichu NIE trafia w `ffmpeg.exe`. Straznik swiecil sie
    wtedy na zielono, chociaz proces zyl.
    """
    needle = pattern.strip().lower()
    if not needle:
        return False
    if needle in name:
        return True
    if fnmatch.fnmatch(name, needle):
        return True
    try:
        return re.search(needle, name) is not None
    except re.error:
        return False


# --------------------------------------------------------------------------- #
# Podglad transkryptu
# --------------------------------------------------------------------------- #
def tail_events(path: Path, count: int = 40, max_bytes: int = 512 * 1024,
                limit_bytes: int = 16 * 1024 * 1024) -> list[dict]:
    """Ostatnie zdarzenia ROZMOWY z .jsonl bez wczytywania calego pliku (bywa >200 MB).

    Okno rosnie, gdy pojedynczy rekord jest wiekszy niz ogon (tak jak w turn_state),
    a rekordy techniczne (attachment, frame-link...) sa pomijane - inaczej polowa
    podgladu to "(attachment)", a rozmowa wypada poza 45 linii.
    """
    window = max_bytes
    while True:
        try:
            lines, truncated = _read_tail(path, window)
        except OSError:
            return []
        events: list[dict] = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if event.get("type") in DECISIVE_RECORD_TYPES or event.get("isCompactSummary"):
                events.append(event)
        if events or not truncated or window >= limit_bytes:
            return events[-count:]
        window *= 4


def describe_event(event: dict) -> tuple[str, str, str]:
    """(czas, kto, tresc) - jedna linia podgladu."""
    ts = str(event.get("timestamp") or "")[11:19]
    kind = str(event.get("type") or "?")
    message = event.get("message") or {}
    content = message.get("content")
    who = {"assistant": t("preview.who.assistant"), "user": t("preview.who.user"),
           "system": t("preview.who.system")}.get(kind) or kind
    if event.get("isSidechain"):
        who = t("preview.who.subagent", who=who)

    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "thinking":
                parts.append(t("preview.thinking"))
            elif btype == "tool_use":
                cmd = ""
                inp = block.get("input") or {}
                for key in ("command", "file_path", "pattern", "prompt", "description"):
                    if inp.get(key):
                        cmd = str(inp[key])
                        break
                parts.append(f"-> {block.get('name', 'tool')}({cmd})")
            elif btype == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                parts.append(t("preview.result", body=body or ""))
    elif event.get("summary"):
        parts.append(str(event["summary"]))

    text = " ".join(" ".join(parts).split())
    return ts, who, (text[:400] or f"({kind})")


__all__ = [
    "TURN_CLOSED",
    "TURN_OPEN",
    "TURN_UNKNOWN",
    "Session",
    "SessionScanner",
    "Verdict",
    "classify_turn",
    "claude_dir",
    "describe_event",
    "evaluate",
    "fmt_duration",
    "is_working",
    "matching_guard_processes",
    "tail_events",
    "turn_state",
]
