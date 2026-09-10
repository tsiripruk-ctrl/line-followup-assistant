from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    line_channel_secret: str = ""
    line_channel_access_token: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"

    owner_line_user_id: str = ""
    owner_display_name: str = "พี่ต้อง"
    timezone: str = "Asia/Bangkok"
    database_url: str = "sqlite:///./followup.db"

    auto_create_confidence: float = 0.86

    # Reminder policy
    reminder_check_seconds: int = 60
    remind_before_due_hours: int = 3
    adaptive_short_notice_hours: int = 1
    adaptive_medium_notice_hours: int = 3
    adaptive_medium_lead_minutes: int = 30
    reminder_repeat_hours: int = 4
    escalation_after_reminders: int = 2

    # Quiet hours (no group follow-up during this window)
    quiet_hour_start: int = 20
    quiet_hour_end: int = 7

    # Owner notifications
    owner_task_ack: bool = True
    owner_status_updates: bool = True
    owner_escalation_alerts: bool = True

    # Daily brief (Thailand/local timezone)
    daily_brief_enabled: bool = True
    morning_brief_hour: int = 7
    morning_brief_minute: int = 30
    evening_brief_hour: int = 18
    evening_brief_minute: int = 30
    # v0.3.2 default: /jobs/tick handles reminders only. Daily briefs use dedicated jobs.
    brief_catchup_on_tick: bool = False

    # Secret for external cron calls. v0.3.2 recommends separate reminder/brief jobs.
    # When set, /jobs/* endpoints require header X-Cron-Secret.
    cron_secret: str = ""

    # v0.4 web dashboard (set a long random secret in Render)
    dashboard_token: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
