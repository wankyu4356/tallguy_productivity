import logging
import sys


class CompactFormatter(logging.Formatter):
    """One-line format optimized for copy-paste troubleshooting."""

    def format(self, record):
        # Short level: I/W/E/D
        level_short = {"INFO": "I", "WARNING": "W", "ERROR": "E", "DEBUG": "D"}.get(
            record.levelname, record.levelname[0]
        )

        # Short module name: app.services.crawler → crawler
        name = record.name.rsplit(".", 1)[-1]

        msg = record.getMessage()

        line = f"[{level_short}] {name}: {msg}"

        if record.exc_info and record.exc_info[1]:
            err_type = type(record.exc_info[1]).__name__
            line += f" | {err_type}: {record.exc_info[1]}"

        return line


def setup_logging(level: str = "INFO"):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CompactFormatter())
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    for noisy in ["selenium", "urllib3", "asyncio", "httpcore", "httpx"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
