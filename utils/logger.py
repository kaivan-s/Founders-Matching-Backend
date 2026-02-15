"""Logging utility for the application"""
import logging
import os
import sys
import json
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

def log_error(message: str, error: Exception = None, traceback_str: str = None, metadata: dict = None):
    """Log error with optional exception, traceback, and metadata"""
    log_msg = message
    if metadata:
        try:
            log_msg = f"{message} | {json.dumps(metadata)}"
        except:
            log_msg = f"{message} | {str(metadata)}"
    
    if error:
        logger.error(f"{log_msg}: {str(error)}", exc_info=error)
    elif traceback_str:
        logger.error(f"{log_msg}\n{traceback_str}")
    else:
        logger.error(log_msg)

def log_warning(message: str, metadata: dict = None):
    """Log warning with optional metadata"""
    log_msg = message
    if metadata:
        try:
            log_msg = f"{message} | {json.dumps(metadata)}"
        except:
            log_msg = f"{message} | {str(metadata)}"
    logger.warning(log_msg)

def log_info(message: str, metadata: dict = None):
    """Log info with optional metadata"""
    log_msg = message
    if metadata:
        try:
            log_msg = f"{message} | {json.dumps(metadata)}"
        except:
            log_msg = f"{message} | {str(metadata)}"
    logger.info(log_msg)

def log_debug(message: str, metadata: dict = None):
    """Log debug (only in development) with optional metadata"""
    log_msg = message
    if metadata:
        try:
            log_msg = f"{message} | {json.dumps(metadata)}"
        except:
            log_msg = f"{message} | {str(metadata)}"
    logger.debug(log_msg)

