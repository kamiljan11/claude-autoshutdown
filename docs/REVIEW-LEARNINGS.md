# Review learnings

Reguły wyciągnięte z dyskusji przy konkretnych PR-ach tego repo. Recenzent (człowiek albo
agent) stosuje je z pierwszeństwem przed regułami ogólnymi — powstały z realnych korekt,
a nie z teorii.

- [2026-09-07] Komunikat dla człowieka podaje POMIAR, nie diagnozę, gdy sygnał jest
  wieloznaczny. Wiszące pytanie o uprawnienia, jedno długo działające narzędzie i czekanie
  na limit API zapisują w transkrypcie ten sam rekord (`turn.tool_in_flight`), a CPU ich nie
  rozróżnia — tekst ma więc wymieniać możliwe przyczyny, nie wybierać jednej. Nie proponuj
  odzyskiwania przyczyny z `turn_reason`; pinują to `test_dlugie_narzedzie_wyglada_tak_samo_jak_wiszaca_zgoda`
  i `test_komunikat_nie_twierdzi_jednej_przyczyny` (źródło: PR #6).
- [2026-09-07] Ten sam napis w słowniku EN, w słowniku PL i w miejscu wywołania `t(...)` to
  idiom tłumaczeń, nie zduplikowany literał — bramka `dup-literals` na takim wzorcu daje
  fałszywy alarm i świadomy wyjątek przy commicie jest tu poprawną decyzją, a nie obejściem
  jakości (źródło: PR #6).
