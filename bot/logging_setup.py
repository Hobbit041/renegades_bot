"""Bounded application logs; routine scheduler heartbeats stay silent."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(directory, max_bytes=5 * 1024 * 1024, backup_count=3):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    rotating = RotatingFileHandler(directory / "camp-bot.log", maxBytes=max_bytes,
                                   backupCount=backup_count, encoding="utf-8")
    for handler in (console, rotating):
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[console, rotating], force=True)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    return rotating
