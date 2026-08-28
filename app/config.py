from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

from app.utils.paths import data_path, env_file

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"

_RETIRED_MODELS = {
    "claude-sonnet-4-20250514": DEFAULT_CLAUDE_MODEL,
    "claude-sonnet-4-5": DEFAULT_CLAUDE_MODEL,
    "claude-sonnet-4-6": DEFAULT_CLAUDE_MODEL,
    "claude-sonnet-4-6-20250627": DEFAULT_CLAUDE_MODEL,
    "claude-3-5-sonnet-20241022": DEFAULT_CLAUDE_MODEL,
    "claude-3-5-sonnet-20240620": DEFAULT_CLAUDE_MODEL,
    "claude-3-sonnet-20240229": DEFAULT_CLAUDE_MODEL,
}


class Settings(BaseSettings):
    # TheBell credentials
    THEBELL_ID: str = ""
    THEBELL_PW: str = ""

    # Claude API
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = DEFAULT_CLAUDE_MODEL

    @field_validator("CLAUDE_MODEL", mode="after")
    @classmethod
    def _upgrade_retired_model(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            return DEFAULT_CLAUDE_MODEL
        return _RETIRED_MODELS.get(v, v)

    # App settings
    OUTPUT_DIR: Path = data_path("output")
    LOG_LEVEL: str = "INFO"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Browser settings
    BROWSER_HEADLESS: bool = True
    CRAWL_TIMEOUT_MS: int = 30000
    NAVIGATION_TIMEOUT_MS: int = 30000
    MAX_CONCURRENT_PAGES: int = 3
    BROWSER_PROFILE_DIR: Path = data_path("browser_profile")

    # Cleanup
    CLEANUP_HOURS: int = 24

    model_config = {
        "env_file": str(env_file()),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def validate_required(self) -> list[str]:
        errors = []
        if not self.ANTHROPIC_API_KEY:
            errors.append("ANTHROPIC_API_KEY is not set")
        return errors

    @property
    def has_thebell_credentials(self) -> bool:
        return bool(self.THEBELL_ID and self.THEBELL_PW)

    @property
    def is_configured(self) -> bool:
        """True once the app has everything it needs to run."""
        return bool(self.ANTHROPIC_API_KEY)


settings = Settings()


def reload_settings() -> Settings:
    """Re-read .env after the setup page writes it, updating `settings` in place.

    Callers hold a reference to the module-level `settings` object, so the
    values are copied onto it rather than rebinding the name.
    """
    fresh = Settings()
    for name in fresh.__class__.model_fields:
        object.__setattr__(settings, name, getattr(fresh, name))
    return settings
