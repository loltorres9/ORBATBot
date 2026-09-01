# ORBAT Lab — Stufe A

Ein eigenständiger Prototyp der Idee, das ORBAT von Google Sheets zu lösen und
im Browser zu bauen. **Er verändert nichts am Bot.** Kein File außerhalb von
`lab/` wurde angefasst, nichts hier wird von `bot.py` oder `web/` importiert,
und umgekehrt importiert `lab/` weder `cogs/` noch `web/` noch `discord.py`.

Zweck: die eine offene Frage beantworten — lässt sich ein ORBAT auf einer Seite
**ohne JavaScript** sinnvoll bauen und pflegen? Alles andere (Discord, OAuth,
Rechte, Sheets) ist bewusst nicht drin.

## Starten

```bash
pip install fastapi uvicorn jinja2 python-multipart
python -m uvicorn lab.devserver:app --reload --port 8081
# → http://127.0.0.1:8081
```

Beim ersten Start legt `lab/seed.py` ein Beispiel-ORBAT samt Einsatz und ein paar
Belegungen an, damit die Seite nicht leer ist. Gespeichert wird in
`lab/orbat_lab.db` (SQLite, von `.gitignore` erfasst). Löschen = zurücksetzen.
`LAB_DB=/pfad/zur.db` legt sie woanders hin.

Tests:

```bash
pip install pytest && python -m pytest lab/tests -q
```

## Was drin ist

| Datei | Inhalt |
|---|---|
| `parser.py` | Das Textformat → Squads und Slots. Rein, ohne Abhängigkeiten. |
| `diff.py` | Vergleicht den neuen Text gegen das Gespeicherte, damit Slot-IDs und damit Belegungen eine Bearbeitung überleben. |
| `render.py` | Board-Aufbau plus Prüfung gegen Discords Embed-Grenzen. |
| `store.py` | SQLite in der Form, die das Postgres-Schema hätte. |
| `devserver.py` | Die Seiten. |
| `seed.py` | Beispiel-ORBAT. |

## Das Textformat

```
1-1 Alpha  | right
  Squad Leader  | unit:TFP
  Rifleman

Reservists  | right, nocount
  Reserve
```

Squad-Zeilen stehen links am Rand, Slots sind eingerückt. Optionen nach `|`:
Squad `left` / `right` / `nocount`, Slot `unit:TAG`. `#` am Zeilenanfang ist ein
Kommentar. Eine führende Nummerierung („1. Rifleman“) wird entfernt, damit aus
einem Sheet kopierte Zeilen direkt passen.

## Die drei Entscheidungen, die geprüft werden sollten

**Ein Textfeld statt eines Slot-Editors.** `web/` hat kein JavaScript und keinen
Build-Step, also gäbe es sonst nur Hoch/Runter-Buttons pro Zeile. Der Text ist
ohne JS beliebig umsortierbar und entspricht der Art, wie ORBATs ohnehin
geschrieben werden. Preis: es gibt kein Live-Update, die Vorschau ist ein
Button.

**Slots tragen keine Belegung.** Wer einen Slot hat, steht in `lab_bookings`,
gekoppelt an `(Einsatz, Slot)` — also da, wo die Produktion es in `requests`
schon führt. Damit ist ein ORBAT eine Vorlage, die beliebig viele Einsätze
trägt, ohne dass etwas zurückgesetzt werden muss.

**Eine Bearbeitung darf niemanden stillschweigend aussetzen.** `diff.py`
ordnet Squads und Slots erst über den Namen zu, dann über die Position; eine
Umbenennung behält deshalb die ID und die Belegung. Alles, was jemanden
austrägt *oder* auf eine andere Rolle verschiebt, geht über eine
Bestätigungsseite, die die betroffenen Leute namentlich nennt.

## Wo das Lab hinter dem echten Editor liegt

Der ORBAT-Editor in `web/` ist inzwischen die echte Umsetzung. Das Lab teilt sich
mit ihm den Parser (`utils/orbat.py`), hat aber **keine Netz-Liste** — die
gemeinsamen Funknetze gibt es nur im echten Editor. Der Funkkanal pro Squad
(`radio:`) wird hier gespeichert.

## Was Stufe A nicht beantwortet

Rechte und OAuth, der Approval-Flow gegen echte Unit-Rollen, die View-Persistenz
über Neustarts und die Migration laufender Requests. Das braucht Stufe B (Lab im
Bot-Prozess hinter `ORBAT_LAB=1`) und Stufe C (eigener Test-Bot).

## Aufräumen

`rm -rf lab/` — sonst nichts.
