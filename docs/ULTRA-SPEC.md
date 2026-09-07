# ULTRA-SPEC — definicja ukończenia Claude AutoShutdown

**Wizja:** osoba uruchamiająca agentów Claude Code na noc zostawia komputer i ma pewność,
że zgaśnie dopiero po zakończeniu pracy, nigdy w trakcie — bez klikania i bez czytania kodu.

Skala: 0 = brak, 5 = działa, 10 = poziom komercyjny (benchmark: narzędzia klasy
"set and forget" jak Caffeine / Amphetamine — użytkownik ufa i zapomina).

| Wymiar | Waga | 2026-09-05 | 2026-09-06 | Cel | Największa luka |
|---|---|---|---|---|---|
| Decyzja "skończone" (nigdy fałszywie) | 3 | 9 | 9 | 9 | sesje CLI **zweryfikowane** 2026-09-06: `claude -p` z npm zapisał `sessions/<pid>.json` po 8 s |
| Nigdy nie wyłączy za wcześnie (bramki) | 3 | 9 | 9 | 9 | — |
| Wyłączy, gdy powinien (nie wisi wiecznie) | 3 | 7 | 8 | 8 | mtime z przyszłości widoczny w logu; Esc-OPEN i wisząca zgoda nadal celowo blokują, ale od 2026-09-07 **krzyczą** o tym w nagłówku i w logu |
| Odporność konfiguracji (zły plik nie wywraca) | 2 | 5 | 8 | 8 | walidacja + zakresy + atomowy zapis + ostrzeżenia w logu |
| Awaria widoczna (nic nie ginie po cichu) | 3 | 6 | 9 | 9 | crash.log + dialog, guard-scan fails closed, timeout = porażka |
| Podgląd (widać, co agent robi) | 1 | 5 | 8 | 8 | subagenci w Podglądzie jako wpisy pod sesją (najświeżsi pierwsi, max 20) |
| Odliczanie (nie męczy, nie kłamie) | 2 | 6 | 8 | 8 | cooldown 60 s, zamrożona akcja/tryb, bell na starcie + 5 s, napis prawdziwy |
| Wydajność w tle (tydzień bez restartu) | 1 | 7 | 8 | 8 | tasklist co 60 s gdy rozbrojony, co cykl gdy uzbrojony |
| Instalacja u obcego | 2 | 6 | 8 | 7 | `ClaudeAutoShutdown.exe` (PyInstaller, onefile, własna ikona) budowany w CI i w Releases; niepodpisany → SmartScreen ostrzega |
| Wielojęzyczność | 1 | 8 | 8 | 8 | — |

**Cykl 2026-09-06 (runda 1):** 57 findingów medium/low → 43 wdrożone (z testami), 8 już
nieaktualne po wcześniejszych zmianach, 5 duplikatów, 1 poza zakresem. Testy 116 → 155,
e2e 13/13. PR #2 zmergowany.

**Cykl 2026-09-06 (runda 2 — Kamil: „rób"):** wszystkie trzy blockery odblokowane:
- `.exe` zbudowany (PyInstaller, własna ikona), CI go buduje i testuje dymnie na
  czystym `windows-latest`, opublikowany jako asset w
  [Release v1.1.0](https://github.com/kamiljan11/claude-autoshutdown/releases/tag/v1.1.0)
  (11,1 MB, bez logowania, zweryfikowane `curl -I` → 302 na publiczny CDN).
- Sesja terminalowa zweryfikowana: `claude -p` z npm zapisał `sessions/<pid>.json`
  w 8 s i posprzątał po zakończeniu.
- Finding #32 (Podgląd ślepy na subagentów) wdrożony: lista subagentów pod sesją,
  najświeższy pierwszy, limit 20, dzienniki workflow odfiltrowane.

Przy okazji znaleziony i naprawiony bug w cudzym hooku `pre-commit` (word-splitting
na spacji w ścieżce repo — blokował workflow-lint na każdym koncie z folderem
zawierającym spację) oraz w CI (`$home` to zmienna tylko-do-odczytu w PowerShell).

Testy 155 → 158, PR #3 zmergowany, `main` = `7633984`.

**Cykl 2026-09-07 (runda 3 — obserwacja z produkcji):** Kamil zobaczył sesję
`claude-code-19` (PID 26356) blokującą wyłączenie od 9 h 53 min przy 0 % CPU i zgłosił
hipotezę „realnie nic nie działało". Śledztwo ją **obaliło**: tura otwarta na `tool_use`
o 23:13:00, przerwa 10 h 03 min, a wynik narzędzia **i realny zapis pliku**
`scheduled-tasks/pg-reviewer-calibration/SKILL.md` dopiero o 09:16:21 — sesja czekała na
zgodę człowieka, praca była w połowie. Bramka zachowała się poprawnie i **została bez
zmian**. Brakowało jednego: powiedzieć wprost, że czeka się na CZŁOWIEKA, nie na agenta.

Wdrożone: `Session.stalled` (tura OPEN + zero subagentów + 30 min bez zapisu), czerwona
linia w nagłówku, czerwony wiersz w tabeli, dzwonek raz, jeden wpis do logu przy wejściu
w stan i przy wyjściu. `is_working()` nietknięte — jest na to osobny test regresyjny.

Recenzja (`code-reviewer`, świeży kontekst, read-only) wyłapała jeden major: pierwsza wersja
tekstu **twierdziła** „zwykle znaczy to, że czeka, aż coś zatwierdzisz", a program tego nie
wie — wiszące pytanie o uprawnienia, jedno narzędzie działające godzinami i czekanie na limit
API dają ten sam rekord `turn.tool_in_flight`, a CPU ich nie rozróżnia (pomiar 2026-09-02).
Poprawione: komunikat podaje POMIAR i wymienia trzy możliwe przyczyny; dwuznaczność opisana
w docstringu `Session.stalled` i przypięta dwoma testami, żeby nikt jej później nie „naprawił"
zgadywaniem po `turn_reason`.

Testy 158 → 171, e2e 13 → **14 etapów** (nowy etap odtwarza PID 26356: 10 h ciszy →
bramka trzyma **i** ostrzeżenie ląduje w logu).

**Wszystkie wymiary osiągnęły cel.** Jedyny pozostały punkt (podpis kodu .exe) jest
decyzją finansową Kamila, nie techniczną — nazwany w README jako znane ograniczenie,
nie blokuje niczego. Pętla ULTRA zakończona: definicja ukończenia spełniona.

**Blockery dla człowieka (nie zgadujemy):**
- ~~Build `.exe`~~ — zrobione 2026-09-06 na decyzję Kamila („rób").
- ~~Test sesji z terminala~~ — zrobione 2026-09-06 (npm `claude -p`, rejestr zapisany po 8 s).
- Podpis kodu (certyfikat) — bez niego SmartScreen ostrzega; decyzja finansowa.
- WSL / drugi `CLAUDE_CONFIG_DIR` — poza zakresem, dokumentujemy.

Zasada cyklu: findingi z `docs/audit-findings.json` (medium/low), każdy z testem,
116 testów + 13/13 e2e zielone przed każdym mergem.
