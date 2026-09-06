# ULTRA-SPEC — definicja ukończenia Claude AutoShutdown

**Wizja:** osoba uruchamiająca agentów Claude Code na noc zostawia komputer i ma pewność,
że zgaśnie dopiero po zakończeniu pracy, nigdy w trakcie — bez klikania i bez czytania kodu.

Skala: 0 = brak, 5 = działa, 10 = poziom komercyjny (benchmark: narzędzia klasy
"set and forget" jak Caffeine / Amphetamine — użytkownik ufa i zapomina).

| Wymiar | Waga | 2026-09-05 | 2026-09-06 | Cel | Największa luka |
|---|---|---|---|---|---|
| Decyzja "skończone" (nigdy fałszywie) | 3 | 9 | 9 | 9 | sesje CLI niezweryfikowane empirycznie (blocker) |
| Nigdy nie wyłączy za wcześnie (bramki) | 3 | 9 | 9 | 9 | — |
| Wyłączy, gdy powinien (nie wisi wiecznie) | 3 | 7 | 8 | 8 | mtime z przyszłości teraz widoczny w logu; Esc-OPEN nadal celowo blokuje |
| Odporność konfiguracji (zły plik nie wywraca) | 2 | 5 | 8 | 8 | walidacja + zakresy + atomowy zapis + ostrzeżenia w logu |
| Awaria widoczna (nic nie ginie po cichu) | 3 | 6 | 9 | 9 | crash.log + dialog, guard-scan fails closed, timeout = porażka |
| Podgląd (widać, co agent robi) | 1 | 5 | 7 | 8 | subagenci nadal nie w Podglądzie (tylko licznik w Monitorze) |
| Odliczanie (nie męczy, nie kłamie) | 2 | 6 | 8 | 8 | cooldown 60 s, zamrożona akcja/tryb, bell na starcie + 5 s, napis prawdziwy |
| Wydajność w tle (tydzień bez restartu) | 1 | 7 | 8 | 8 | tasklist co 60 s gdy rozbrojony, co cykl gdy uzbrojony |
| Instalacja u obcego | 2 | 6 | 6 | 7 | brak instalatora (blocker: decyzja o exe) |
| Wielojęzyczność | 1 | 8 | 8 | 8 | — |

**Cykl 2026-09-06:** 57 findingów medium/low → 43 wdrożone (z testami), 8 już nieaktualne
po wcześniejszych zmianach, 5 duplikatów, 1 poza zakresem (WSL / drugi katalog konfiguracji).
Szczegóły: `docs/audit-status.md`. Testy 116 → 155, e2e 13/13.

**Pozostałe wymiary poniżej celu:** wszystkie czekają na blocker (exe, test CLI).
Pętla zatrzymana na terminacji: „zostały tylko wymiary czekające na człowieka".

**Blockery dla człowieka (nie zgadujemy):**
- Build `.exe` (PyInstaller) — decyzja o dystrybucji.
- Test sesji z terminala na maszynie, gdzie npm-owy `claude` działa.
- WSL / drugi `CLAUDE_CONFIG_DIR` — poza zakresem, dokumentujemy.

Zasada cyklu: findingi z `docs/audit-findings.json` (medium/low), każdy z testem,
116 testów + 13/13 e2e zielone przed każdym mergem.
