"""
Logging configuration for Movie Searcher.

IMPORTANT: Do NOT use emojis or Unicode symbols (checkmarks, X marks, etc.) in log messages.
Keep log messages plain text only for compatibility and readability.
"""
import sys
import logging
from pathlib import Path

# Log file in root directory
LOG_FILE = Path(__file__).parent.parent / "movie_searcher.log"

# Ensure UTF-8 output for console to avoid UnicodeEncodeError on Windows consoles
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    # Safe to ignore; file handler below still uses UTF-8
    pass


# Global shutdown flag for filter (set during shutdown)
_app_shutting_down = False


def set_app_shutting_down(value: bool):
    """Set the app shutting down flag for logging filters"""
    global _app_shutting_down
    _app_shutting_down = value


class ConsoleLogFilter(logging.Filter):
    """Filter to suppress verbose logs from video_processing module on console"""
    def filter(self, record):
        # Allow all logs that are WARNING or above
        if record.levelno >= logging.WARNING:
            return True
        # Suppress INFO/DEBUG logs from video_processing module
        if record.name.startswith('video_processing'):
            return False
        # Allow all other logs
        return True


class SuppressShutdownErrorsFilter(logging.Filter):
    """Filter to suppress known harmless errors during shutdown"""
    def filter(self, record):
        # Suppress AssertionError from asyncio during shutdown (Windows ProactorEventLoop issue)
        if record.levelno == logging.ERROR:
            if 'asyncio' in record.name:
                msg = record.getMessage() if hasattr(record, 'getMessage') else str(record.msg)
                if 'AssertionError' in msg or '_attach' in msg:
                    return False
            # Suppress ffmpeg errors during shutdown (processes being killed is expected)
            if 'video_processing' in record.name:
                msg = record.getMessage() if hasattr(record, 'getMessage') else str(record.msg)
                if '_ffmpeg_job failed' in msg and _app_shutting_down:
                    return False
        return True


def setup_logging():
    """Configure root logger with file and console handlers"""
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture everything

    # File handler: logs everything (DEBUG and above) with shutdown error suppression
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    file_handler.addFilter(SuppressShutdownErrorsFilter())
    root_logger.addHandler(file_handler)

    # Console handler: only logs INFO and above, with filtering for video_processing
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(ConsoleLogFilter())
    console_handler.addFilter(SuppressShutdownErrorsFilter())
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    return root_logger

