# v0.6.43 – Natural Task Search

- Owner private chat accepts bare search terms such as `ชุมแสง`, `Meter`, `Futong`.
- Explicit `ค้นหา <term>` uses the same smart search.
- Searches task code/title/project/assignee/notes/progress/waiting/next action and TaskEvent timeline.
- Searches all statuses, including completed history.
- Adds conservative synonym normalization (e.g. Meter ↔ มิเตอร์).
- Search is read-only: it never creates, closes, reopens, reschedules, or edits a task.
- No DB migration or new environment variable.
