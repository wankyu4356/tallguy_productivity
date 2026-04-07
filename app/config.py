from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # TheBell credentials
    THEBELL_ID: str = ""
    THEBELL_PW: str = ""

    # Claude API
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"

    # App settings
    OUTPUT_DIR: Path = Path("./output")
    LOG_LEVEL: str = "INFO"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Browser settings
    BROWSER_HEADLESS: bool = True
    CRAWL_TIMEOUT_MS: int = 30000
    NAVIGATION_TIMEOUT_MS: int = 15000
    MAX_CONCURRENT_PAGES: int = 3

    # Cleanup
    CLEANUP_HOURS: int = 24

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def validate_required(self) -> list[str]:
        errors = []
        if not self.ANTHROPIC_API_KEY:
            errors.append("ANTHROPIC_API_KEY is not set")
        return errors


settings = Settings()
