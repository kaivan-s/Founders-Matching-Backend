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


def sanitize_error_for_user(error: Exception) -> str:
    """
    Convert database/internal errors into user-friendly messages.
    Logs the original error for debugging but returns a clean message.
    """
    error_str = str(error)
    
    # Database constraint errors
    if 'violates not-null constraint' in error_str:
        log_error("Database constraint error (hidden from user)", error=error)
        return "Something went wrong. Please try again."
    
    if 'violates unique constraint' in error_str:
        log_error("Database unique constraint error", error=error)
        return "This record already exists."
    
    if 'violates foreign key constraint' in error_str:
        log_error("Database foreign key error", error=error)
        return "Related data not found. Please refresh and try again."
    
    # Connection/timeout errors
    if 'connection' in error_str.lower() or 'timeout' in error_str.lower():
        log_error("Database connection/timeout error", error=error)
        return "Connection issue. Please try again in a moment."
    
    # PostgreSQL error codes (from Supabase)
    if "'code':" in error_str or '"code":' in error_str:
        log_error("Database error with code (hidden from user)", error=error)
        return "Something went wrong. Please try again."
    
    # If it's already a clean ValueError message, keep it
    if isinstance(error, ValueError):
        return error_str
    
    # Default: hide technical details
    if len(error_str) > 100 or '{' in error_str or 'Error' in error_str:
        log_error("Internal error (hidden from user)", error=error)
        return "Something went wrong. Please try again."
    
    return error_str

