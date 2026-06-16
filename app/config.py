from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings

# Current default model. Retired model IDs found in an existing .env are
# transparently upgraded to this so the app keeps working without the user
# having to hand-edit their .env file.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"

# Retired/removed model IDs → current replacement. The Anthropic API returns
# 404 not_found_error for these, which previously broke recommendation and
# classification entirely.
_RETIRED_MODELS = {
    "claude-sonnet-4-20250514": DEFAULT_CLAUDE_MODEL,
    "claude-sonnet-4-6-20250627": DEFAULT_CLAUDE_MODEL,  # never a valid dated id
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
    OUTPUT_DIR: Path = Path("./output")
    LOG_LEVEL: str = "INFO"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Browser settings
    BROWSER_HEADLESS: bool = True
    CRAWL_TIMEOUT_MS: int = 30000
    NAVIGATION_TIMEOUT_MS: int = 30000
    MAX_CONCURRENT_PAGES: int = 3
    BROWSER_PROFILE_DIR: Path = Path("./browser_profile")

    # Cleanup
    CLEANUP_HOURS: int = 24

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def validate_required(self) -> list[str]:
        errors = []
        if not self.ANTHROPIC_API_KEY:
            errors.append("ANTHROPIC_API_KEY is not set")
        return errors

    @property
    def has_thebell_credentials(self) -> bool:
        return bool(self.THEBELL_ID and self.THEBELL_PW)


settings = Settings()
