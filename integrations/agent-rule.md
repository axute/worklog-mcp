# Agent rule: when to log

The server already tells clients what `log_entry` is for, but agents log far more
reliably with an explicit rule in the instructions they load in every session.
Copy one of the two blocks below (English or German) into:

| Client | Where |
|---|---|
| Claude Code | `~/.claude/CLAUDE.md` (all projects on this machine) or a project's `CLAUDE.md` |
| claude.ai / Claude Desktop | *Settings → General → personal preferences*, or the instructions of a project |
| Other agents | their global instructions, e.g. `AGENTS.md` or the IDE's guidelines file |

The tool is called `worklog.log_entry` here; if you named the server differently
when adding it, use that name.

## English

```markdown
## Work log (worklog)
After every finished work step – feature, fix, configuration change, research
result, decision – call worklog.log_entry:
- project: repository or topic name. Check worklog.list_projects first and use an
  existing name exactly if it fits.
- summary: one sentence about the result (what is different now), not the process.
- details: important commands, changed files, decisions and reasons.
- tags and duration_min (estimated) where useful.
One entry per result, not per command. Pure questions and explanations without a
result are not logged. Never log secrets (tokens, passwords, keys, private data).
If the worklog tool is not available in a session, mention it once and continue
without logging.
```

## Deutsch

```markdown
## Arbeitsprotokoll (worklog)
Nach jedem abgeschlossenen Arbeitsschritt – Feature, Fix, Konfigurationsänderung,
Recherche-Ergebnis, Entscheidung – rufe worklog.log_entry auf:
- project: Repo- oder Themenname. Vorher mit worklog.list_projects prüfen, ob es das
  Projekt schon gibt, und genau diesen Namen verwenden.
- summary: ein Satz zum Ergebnis (was ist jetzt anders), nicht zum Vorgehen.
- details: wichtige Befehle, geänderte Dateien, Entscheidungen und Gründe.
- tags und duration_min (geschätzt), wenn sinnvoll.
Ein Eintrag pro Ergebnis, nicht pro Einzelbefehl. Reine Fragen und Erklärungen ohne
Ergebnis werden nicht protokolliert. Niemals Geheimnisse (Tokens, Passwörter, Keys,
private Daten) eintragen.
Ist das worklog-Tool in einer Sitzung nicht verfügbar, einmal kurz darauf hinweisen
und ohne Protokoll weiterarbeiten.
```
