"""Console and rotating UTF-8 file logging."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from cloudbridge.config import Config
from cloudbridge.utils.paths import native_path


def configure_logging(config: Config) -> None:
    path = native_path(config.logging.file)
    path.parent.mkdir(parents=True, exist_ok=True)
    file = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=config.app.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), file], force=True)
