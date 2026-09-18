# v0.6.41 – Owner Custom Tone Preservation Fix

## Fixes
- Preserves the owner-entered custom tone instruction instead of collapsing it to a preset label.
- Adds a first-class `secretary_natural` runtime tone for instructions containing “ธรรมชาติ/เลขานุการ”.
- Cleans obvious accidental repeated-key noise such as `sssธรรมชาติ` without removing normal English words.
- Applies the custom tone during reminder rendering; this is not display-only metadata.
- Owner policy summary now shows the cleaned custom instruction exactly.
- Adds multi-line `ห้ามใช้คำ:` command, `ดูคำห้ามใช้`, and `ล้างคำห้ามใช้ทั้งหมด`.

## Database
No schema migration. Existing `owner_preferences.followup_policy` JSON is reused.

## Environment variables
None.

## Rollback
Deploy v0.6.40. The saved JSON remains backward-compatible.

## Manual smoke test
1. `ตั้งโทนติดตาม: เป็น sssธรรมชาติเหมือน เลขานุการ สุภาพ กระชับ ไม่กดดัน`
2. `ดูรูปแบบการติดตามปัจจุบัน`
3. Expected: tone displays the cleaned custom instruction, not “เป็นกันเองแบบมืออาชีพ”.
4. `ทดลองข้อความติดตาม`
5. Expected: preview respects the natural/soft/concise traits.
6. `ห้ามใช้คำ:\n- ขออัปเดต\n- มีความคืบหน้าเพิ่มเติมไหมคะ`
7. `ดูคำห้ามใช้`
8. Expected: both phrases are listed and omitted from previews.
