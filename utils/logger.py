"""Logging utility for the application"""
import logging
import os
import sys
from datetime import datetime

# Configure logging
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger('founders_matching')

def log_error(message: str, error: Exception = None, traceback_str: str = None):
    """Log error with optional exception and traceback"""
    if error:
        logger.error(f"{message}: {str(error)}", exc_info=error)
    elif traceback_str:
        logger.error(f"{message}\n{traceback_str}")
    else:
        logger.error(message)

def log_warning(message: str):
    """Log warning"""
    logger.warning(message)

def log_info(message: str):
    """Log info"""
    logger.info(message)

def log_debug(message: str):
    """Log debug (only in development)"""
    logger.debug(message)

