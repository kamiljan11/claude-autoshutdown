# Claude AutoShutdown

Wyłącza komputer dopiero wtedy, gdy **wszystkie** sesje Claude Code / Cowork —
razem z subagentami — faktycznie skończą pracę. Idziesz spać, agenci robią swoje,
maszyna gaśnie sama.

## Po co to istnieje

Claude Code nie ma wbudowanego „wyłącz komputer, jak skończysz". Odpalasz długie zadanie —
audyt, refaktor, pętlę agentów — i zostają dwie złe opcje: **zostawić komputer na całą noc**
(agenci kończą o 2:00, maszyna buczy do rana; to samo, gdy wychodzisz z domu na kilka godzin),
albo **timer / systemowe usypianie** — oba działają na zegar, nie na *pracę*. Timer utnie agenta
w połowie edycji, a usypianie Windows liczy *Twoją* mysz, nie aktywność agenta, więc ludzie je
wyłączają i wracamy do opcji pierwszej.

Ten program zamienia „wyłącz o 3:00" na **„wyłącz, gdy robota jest naprawdę skończona"** — i jest
celowo paranoiczny co do tego, co „skończona" znaczy.

**Język:** interfejs po polsku i angielsku. Domyślnie bierze język Windows, przełącznik `PL`/`EN`
w nagłówku. W `config.json`: `"language": "auto"` / `"en"` / `"pl"`.

Uruchomienie: **`Claude AutoShutdown.vbs`** (bez okna konsoli).
Diagnostyka: `Uruchom z konsola (diagnostyka).bat`.

**Autostart:** skrót w `Startup` (`Claude AutoShutdown.lnk` → `wscript.exe` + launcher).
Program wstaje po każdym zalogowaniu. Usunięcie skrótu wyłącza autostart.
Program startuje **rozbrojony** — po restarcie kliknij UZBRÓJ, albo włącz
„Uzbrajaj się sam przy starcie" w Ustawieniach (`arm_on_start`), żeby nie klikać wcale.

---

## Jak wykrywa, że sesja skończyła

Nie zgaduje po CPU ani po tym, że „nic się nie dzieje". Czyta to samo, co Claude Code
zapisuje na dysku:

| Źródło | Co daje |
|---|---|
| `~/.claude/sessions/<PID>.json` | rejestr żywych sesji: PID, sessionId, katalog, `procStart`, nazwa |
| `~/.claude/projects/<slug>/<sessionId>.jsonl` | transkrypt — `mtime` rośnie przy każdym zapisie |
| `.../<sessionId>/subagents/**/*.jsonl` | subagenci — także ci z Workflow, w podkatalogach |
| ostatni rekord transkryptu | **stan tury** — to najważniejszy sygnał |

### Stan tury bije ciszę pliku

Sama cisza w pliku kłamie. Podczas `/compact` albo czekania na limit API transkrypt
stoi w miejscu przez minuty, a sesja **nie skończyła**. Dlatego program czyta ogon
transkryptu i klasyfikuje ostatni znaczący rekord:

| Rekord | Stan | Znaczenie |
|---|---|---|
| `assistant` + `stop_reason: end_turn` | ZAMKNIĘTA | model odpowiedział, czeka na Ciebie |
| `assistant` + `stop_reason: tool_use` | OTWARTA | narzędzie w toku |
| `user` (tool_result albo prompt) | OTWARTA | model liczy / kompaktuje / czeka na limit |
| `isCompactSummary` | OTWARTA | kompaktowanie kontekstu |
| cokolwiek innego | NIEZNANA | **blokuje wyłączenie** |

**Niewiedza nie jest zgodą na wyłączenie.** Nieczytelny plik, nieznany format rekordu,
awaria odczytu — każde z tego daje stan NIEZNANY, który blokuje tak samo jak otwarta tura.

Dwie rzeczy, które to psuły, zanim zostały naprawione:

- **Okno odczytu jest adaptacyjne.** Pojedynczy rekord bywa większy niż cały ogon:
  w transkryptach na tej maszynie jest **494 rekordów ponad 256 KB, największy 5,1 MB**,
  i prawie wszystkie to `user` z wynikiem narzędzia — czyli dokładnie moment „model
  dostał wynik i liczy dalej". Przy stałym oknie ogon nie zawierał ani jednej pełnej
  linii. Program zaczyna od 256 KB i sięga głębiej (do 16 MB), dopóki nie znajdzie rekordu.
- **Lista dozwolonych, nie lista szumu.** O turze decydują wyłącznie rekordy `assistant`
  i `user`. W żywych transkryptach są też `frame-link` (946), `pr-link` (155),
  `artifact-*` (30), `permission-mode` — dopisane po napisaniu programu. Przy liście
  szumu każdy nowy typ kasowałby wykrytą otwartą turę.

Sesja jest BEZCZYNNA tylko wtedy, gdy tura jest zamknięta **i** transkrypt milczy
dłużej niż próg **i** nie ma świeżych subagentów.

### Dlaczego CPU się nie liczy

Zmierzone na tej maszynie (6 próbek po 5 s, 2026-09-02): sesje z **zamkniętą** turą
chodziły na 0,9–2,0 % ze szczytami do **5,0 %**, a sesja realnie pracująca na 2,7 %
ze szczytem 5,3 %. Rozkłady się pokrywają — CPU nie odróżnia pracy od bezczynności.
Dlatego CPU **w ogóle nie bierze udziału w decyzji** — zostaje jako kolumna
w Monitorze, do patrzenia, nie do decydowania. Przy pierwotnym progu 3 % każda
bezczynna sesja fałszywie raportowała „PRACUJE" i komputer nie zgasłby nigdy.

### Subagenci w podkatalogach

Zwykli subagenci leżą wprost w `subagents/`, ale agenci uruchomieni przez Workflow
siedzą w `subagents/workflows/wf_*/`. Przeszukiwanie jest **rekurencyjne** — płaskie
ich nie widziało, a to właśnie one mielą godzinami po tym, jak główna sesja zamilkła.

### Martwe sesje

Po zabitym procesie plik sesji zostaje na dysku. Program odrzuca takie śmieci
dwuetapowo: PID musi żyć **i** czas startu procesu musi zgadzać się z `procStart`
z pliku (tolerancja 1 s). To chroni przed recyklingiem PID przez Windows.

---

## Bezpieczniki (żeby nie wyłączyło przypadkiem)

1. Start zawsze **ROZBROJONY** — uzbrojenie nigdy nie dziedziczy się z pliku
   konfiguracyjnego, każde uruchomienie wymaga świadomego kliknięcia.
   Tryb prób jest domyślnie włączony, ale gdy raz go świadomie zdejmiesz,
   zostaje zdjęty także po restarcie.
2. Uzbrojenie wymaga kliknięcia i potwierdzenia listy reguł.
3. Wszystkie warunki muszą być zielone **N razy z rzędu** (domyślnie 3 cykle).
   Jedno migotanie zeruje licznik.
4. Odliczanie (domyślnie 90 s) z wielkim **ANULUJ** i klawiszem `Esc`. Powrót
   dowolnej sesji do pracy przerywa je automatycznie. Przycisk „Wykonaj teraz"
   nie przyjmuje focusu klawiatury — przypadkowa spacja anuluje, nigdy nie przyspiesza.
5. Wymóg bezczynności użytkownika (domyślnie 10 min bez ruchu myszy).
6. **Plik `STOP`** w katalogu programu blokuje akcję niezależnie od wszystkiego:
   ```bash
   type nul > "STOP"
   ```
7. Procesy-strażnicy — dopóki żyje proces pasujący do wzorca (np. `ffmpeg`),
   nic się nie wyłącza.
8. **Jedna instancja naraz** — druga kopia programu wykrywa żywą pierwszą
   (PID + czas startu procesu) i zamyka się, zamiast wykonać akcję dwa razy.
   Plik blokady po awarii nie blokuje niczego.
9. **Awaria skanera blokuje.** „Nic nie widzę" nie znaczy „nic nie pracuje" —
   błąd odczytu rejestru jest osobnym, czerwonym warunkiem.
10. **Kontrola krzyżowa z systemem** — proces sesji Claude Code żyjący bez wpisu
    w rejestrze blokuje wyłączenie. Procesy pomocnicze aplikacji desktopowej
    (renderer, GPU, crashpad) są odfiltrowane po ścieżce, żeby nie było fałszywych alarmów.
11. **Zniknięcie ostatniej sesji wymaga ciszy** — zamknięcie okna Claude nie gasi
    komputera po 30 s, tylko po tym samym progu ciszy co zwykle.
12. **Rozbrojenie przerywa odliczanie**, a `execute_action` sprawdza tuż przed akcją:
    uzbrojenie, plik STOP i świeżość danych skanera.
13. **Pilnowacz pilnuje sam siebie** — pętla GUI nie może umrzeć po cichu na wyjątku,
   a gdy skaner przestanie dostarczać dane, nagłówek zmienia się na `MONITOR MILCZY`
   / `MONITOR PADL`. Zamarły monitor nie może wyglądać jak „wszystko spokojnie".
14. Każda decyzja trafia do `autoshutdown.log` (rotacja przy 5 MB → `.log.1`),
    razem z **powodem czekania** — log zapisuje każdą zmianę zestawu blokerów,
    więc na pytanie „czemu rano komputer nadal chodzi" jest odpowiedź, nie zgadywanka.

---

## Zakładki

- **Monitor** — lista żywych sesji (stan, **dlaczego**, cisza, CPU, subagenci, PID)
  plus checklista wszystkich warunków: widać dokładnie, co blokuje wyłączenie.
- **Podgląd** — ostatnie zdarzenia z transkryptu wybranej sesji: co Claude właśnie robi.
  Podwójne kliknięcie sesji w Monitorze przenosi tutaj.
- **Ustawienia** — progi, akcja, tryb prób, strażnicy + self-check uprawnień.
- **Log** — historia decyzji.

## Ustawienia

| Pole | Domyślnie | Znaczenie |
|---|---|---|
| Cisza sesji | 300 s | ile bez zapisu = sesja skończyła |
| Co ile sprawdzać | 10 s | częstotliwość skanu |
| Potwierdzeń z rzędu | 3 | tyle cykli pod rząd musi być czysto |
| Odliczanie | 90 s | czas na anulowanie |
| Bezczynność użytkownika | 600 s | ile nie ruszasz myszy |
| Akcja | `shutdown` | `shutdown` / `hibernate` / `sleep` / `lock` / `nothing` |
| Wymuś zamknięcie aplikacji | włączone | `/f` — Windows nie pokaże ekranu blokującego |
| Uzbrajaj się sam przy starcie | wyłączone | zero klikania po restarcie |
| Tryb prób | włączony | **zdejmij, żeby realnie wyłączało** |

Konfiguracja: `config.json` obok programu. Katalog stanu można przenieść zmienną
`CLAUDE_AUTOSHUTDOWN_HOME`.

---

## Żadnych okienek — ani UAC, ani „aplikacja blokuje zamknięcie"

Program ma zgasić komputer sam, w nocy, gdy nikogo nie ma przy klawiaturze.
Dlatego żaden krok nie może czekać na kliknięcie.

**UAC nie wystąpi.** Wyłączenie własnej maszyny nie wymaga administratora —
potrzebny jest tylko przywilej `SeShutdownPrivilege`, który zwykłe konto ma.
Zweryfikowane na tym komputerze: `shutdown /s /t 600` z konta **bez elewacji**
zwróciło **exit 0** (potem natychmiast anulowane). Żadnego okna „czy zezwolić".

**Ekran blokujący też nie.** Bez flagi `/f` Windows potrafi wyświetlić „Ta aplikacja
uniemożliwia zamknięcie" i czekać. Dlatego akcja `shutdown` używa
`shutdown /s /t 0 /f`. Cena: niezapisana praca w innych programach przepada.
Można to wyłączyć w Ustawieniach („Wymuś zamknięcie aplikacji"), godząc się na
ryzyko, że komputer nie zgaśnie.

**Program sprawdza się sam.** Przy uzbrajaniu weryfikuje przywilej i **odmawia
uzbrojenia**, jeśli wybranej akcji nie da się wykonać — zamiast obiecywać coś,
czego nie dowiezie. Wynik tego sprawdzenia widać też w Ustawieniach, razem
z listą stanów zasilania wspieranych przez system.

**Cicha porażka jest wykrywana.** Kod wyjścia komendy jest sprawdzany. Gdyby
`shutdown.exe` odrzucił polecenie, program zapisze `AKCJA NIEUDANA` z pełnym
komunikatem, rozbroi się i pokaże błąd — zamiast zameldować sukces i zostawić
włączony komputer bez śladu dlaczego.

---

## Testy

```bash
python -m pytest -q
```

116 testów: logika decyzyjna, wykrywanie stanu tury, akcje zasilania, uprawnienia, blokada instancji i konfiguracja.

```bash
python ultimate_test.py
```

Test końcowy na **sztucznym** środowisku: tworzy tymczasowy `~/.claude`, uruchamia
prawdziwe procesy `claude.exe`, przepuszcza program przez 13 etapów (otwarta tura,
kompaktowanie, dwie sesje, wszystko skończone, martwy PID, rekord większy niż okno
odczytu, nieznany typ rekordu, awaria skanera, sesja spoza rejestru, zniknięcie
ostatniej sesji, GUI end-to-end w trybie bojowym z akcją `nothing`) i zapisuje `ultimate_test_report.log`. Prawdziwej maszyny
nie dotyka.

---

## Ograniczenia — czytaj przed zaufaniem na noc

- Sesja czekająca na Twoją odpowiedź na pytanie o uprawnienia ma **otwartą turę**,
  więc liczy się jako pracująca i zablokuje wyłączenie. To celowe, ale znaczy tyle,
  że wisząca zgoda trzyma komputer włączony.
- Program nie wie nic o pracy **poza** Claude Code — trwający `git push`, render,
  upload. Do tego służą procesy-strażnicy.
- Sesja, której proces padł w trakcie `tool_use`, zostawia otwartą turę w transkrypcie.
  Sam plik sesji zostanie odrzucony (martwy PID), więc nie zablokuje wyłączenia.
- `sleep` na maszynie z włączoną hibernacją zwykle hibernuje — to zachowanie Windows,
  nie programu.
