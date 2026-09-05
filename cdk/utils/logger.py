"""Colorized stdlib logger, ported from the super-fiesta pattern."""

import logging
import os
import time


class CustomFormatter(logging.Formatter):
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    if os.getenv("LOG_LEVEL") == "DEBUG":
        fmt = (
            "%(asctime)s | %(levelname)s | %(message)s "
            "| (%(filename)s:%(lineno)d)"
        )
    else:
        fmt = "%(asctime)s | %(levelname)s | %(message)s"

    FORMATS = {
        logging.DEBUG: grey + fmt + reset,
        logging.INFO: grey + fmt + reset,
        logging.WARNING: yellow + fmt + reset,
        logging.ERROR: red + fmt + reset,
        logging.CRITICAL: bold_red + fmt + reset,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


def configure_logger(logger_name: str) -> logging.Logger:
    """Create a logger with the colorized formatter (INFO by default)."""
    logger = logging.getLogger(f"{logger_name}{time.time_ns()}")
    log_level = os.environ.get("LOG_LEVEL", logging.INFO)
    logger.setLevel(log_level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(CustomFormatter())
    logger.addHandler(stream_handler)
    return logger
