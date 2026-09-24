# v0.6.46 – Natural Mentioned Assignment Routing Fix

## Problem
Natural owner wording such as `@ตี๋ ฝากเรื่องงาน อบต หนองชิ่ม ในความก้าวหน้าของงานหน่อยนะ` was not auto-created because `ฝากเรื่องงาน` was not recognized as an actionable request. If the LLM extracted a task, the Creation Consent Guard could still block it because there was no explicit work-request signal.

## Fix
- Added `is_natural_assignment_request()` for natural assignment phrases such as `ฝากเรื่องงาน`, `ฝากงาน`, `มีอีกเรื่อง`, `มีเรื่องใหม่`, and `ฝากเรื่อง`.
- The route is enabled only when LINE metadata confirms a real human @mention.
- Natural assignment semantics take precedence over generic progress/follow-up wording.
- Existing active-task duplicate matching still runs before task creation. If a matching task already exists, the system adds a FOLLOW_UP event instead of creating a duplicate FU.
- Quiet-by-default remains unchanged for casual messages without an actionable assignment.

## Expected example
`@ตี๋ ฝากเรื่องงาน อบต หนองชิ่ม ในความก้าวหน้าของงานหน่อยนะ`

No existing matching task -> create a new FU assigned to ตี๋.
Existing matching task -> continue the existing FU, do not duplicate.

## Database / Environment
No migration. No new environment variables.

## Rollback
Deploy v0.6.45.
