# v0.6.44 – Natural Quoted Completion Fix

- Exact LINE quote replies now accept natural whole-task confirmations such as `เรียบร้อย`, `เสร็จ`, `เสร็จเรียบร้อย`, and polite suffix variants.
- Question, negation/waiting, and milestone safety guards still take absolute priority.
- No database migration or new environment variable.
- Regression coverage expanded for short Thai completion replies.
