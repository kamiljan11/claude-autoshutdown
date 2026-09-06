<!-- MAS PR template (2026-09-05). Jeden ekran. Zrodla: TanStack (release impact + "I fully understand this code"), sqlite (bug = najpierw failing test), hono/zod (zero zaleznosci bez powodu), anti-sycophancy (dowod, nie deklaracja). -->

## Zmiany (co + dlaczego, 1-3 linie)


## Zakres
Jeden temat. Zmiany niezwiazane -> osobny PR. Tier (z `node ~/.claude/hooks/lib/risk-tier.js`): **T_**

## Checklista
- [ ] `npm run lint` + `tsc -b`/`--noEmit` + `npm test -- --run` przechodza lokalnie (komendy + exit code ponizej)
- [ ] Bugfix: istnieje test, ktory BEZ tej zmiany pada
- [ ] Nowa logika ma test asertujacy zachowanie (nie implementacje); nie edytowano testu razem z kodem, ktory testuje, bez powodu
- [ ] Rozumiem w pelni ten kod, lacznie z fragmentami wygenerowanymi przez AI
- [ ] Zero nowych zaleznosci runtime (albo uzasadnienie + ADR ponizej)
- [ ] Zero sekretow w diffie; nowe env w `.env.example` bez wartosci
- [ ] Zero nowych `eslint-disable` / `@ts-ignore` / `as any` / pustych `catch`
- [ ] T2+: `pg-review` odpalony — link do `aggregated.md` ponizej

## Wplyw na release
- [ ] widoczne dla usera -> wpis w `CHANGELOG.md [Unreleased]`
- [ ] tylko docs/CI/dev -> bez changelogu
- [ ] migracja / edge fn -> krok deployu opisany (Lovable: Publish reczny + `supabase functions deploy`; Vercel: push)
- [ ] rollback: jak cofnac w < 5 min

## Jak zweryfikowalem (komenda + obserwowany wynik, nie „dziala")
```
```

## Pominiete / zalozenia / do decyzji Kamila

