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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
