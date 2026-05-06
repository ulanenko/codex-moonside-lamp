from __future__ import annotations

import logging
from pathlib import Path


def setup_file_logger(
    name: str,
    path: str | None,
    *,
    debug: bool = False,
    console: bool = False,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    if path:
        try:
            log_path = Path(path).expanduser()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.FileHandler(log_path, encoding="utf-8")
        except OSError:
            handler = logging.NullHandler()
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.addHandler(handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        logger.addHandler(console_handler)

    return logger
