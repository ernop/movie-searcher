"""Uvicorn server configuration and startup"""
import uvicorn
import signal
import sys
import atexit

# Import from main module
from main import app, shutdown_flag, kill_all_active_subprocesses, logger
import main
from utils.logging import LOG_FILE

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    logger.info("Received interrupt signal, shutting down...")
    # Set shutdown flag in main module for filter
    main._app_shutting_down = True
    shutdown_flag.set()
    kill_all_active_subprocesses()
    sys.exit(0)

def get_uvicorn_log_config():
    """Get uvicorn logging configuration"""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(levelname)s - %(message)s",
            },
            "access": {
                "format": "%(asctime)s - %(levelname)s - %(message)s",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "file_default": {
                "formatter": "default",
                "class": "logging.FileHandler",
                "filename": str(LOG_FILE),
                "encoding": "utf-8",
            },
            "file_access": {
                "formatter": "access",
                "class": "logging.FileHandler",
                "filename": str(LOG_FILE),
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "uvicorn.error": {
                "handlers": ["default", "file_default"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access", "file_access"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }

def run_server(open_browser_url: str | None = None, dev: bool = False):
    """Run the uvicorn server with configured settings
    
    Args:
        open_browser_url: If set, opens this URL in browser once server is ready
        dev: If True, enables auto-reload on .py file changes
    """
    # Set browser URL in main module (checked during lifespan startup)
    if open_browser_url:
        main._open_browser_url = open_browser_url
    
    # Register atexit handler for cleanup on exit
    atexit.register(lambda: (shutdown_flag.set(), kill_all_active_subprocesses()))
    
    # Register signal handlers for clean shutdown
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    
    # Get uvicorn log configuration
    uvicorn_log_config = get_uvicorn_log_config()
    
    try:
        logger.info("=" * 60)
        logger.info("Starting Movie Searcher server")
        logger.info("Server URL: http://127.0.0.1:8002")
        if dev:
            logger.info("DEV MODE: Auto-reload enabled for *.py files")
        logger.info("=" * 60)
        
        # Build uvicorn config
        uvicorn_kwargs = {
            "app": "main:app",
            "host": "127.0.0.1",
            "port": 8002,
            "use_colors": False,
            "log_config": uvicorn_log_config,
        }
        
        if dev:
            # Dev mode: enable reload watching only .py files
            uvicorn_kwargs.update({
                "reload": True,
                "reload_includes": ["*.py"],
                "reload_excludes": ["*.pyc", "__pycache__/*", ".git/*"],
            })
        else:
            uvicorn_kwargs["reload"] = False
        
        uvicorn.run(**uvicorn_kwargs)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        shutdown_flag.set()
        kill_all_active_subprocesses()
        sys.exit(0)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Movie Searcher Server")
    parser.add_argument("--dev", action="store_true", help="Enable dev mode with auto-reload on .py changes")
    parser.add_argument("--open-browser", action="store_true", help="Open browser on startup")
    args = parser.parse_args()
    
    browser_url = "http://127.0.0.1:8002" if args.open_browser else None
    run_server(open_browser_url=browser_url, dev=args.dev)

