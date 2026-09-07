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
    reminder_check_seconds: int = 60
    auto_create_confidence: float = 0.86
    remind_before_due_hours: int = 3
    reminder_repeat_hours: int = 4
    quiet_hour_start: int = 20
    quiet_hour_end: int = 7
    owner_task_ack: bool = True
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
