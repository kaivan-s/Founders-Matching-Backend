"""Rate limiting configuration for API endpoints"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os

def get_rate_limit_key():
    """Get rate limit key function - uses Clerk user ID if available, otherwise IP"""
    from flask import request
    clerk_user_id = request.headers.get('X-Clerk-User-Id')
    if clerk_user_id:
        return f"user:{clerk_user_id}"
    return get_remote_address()

def init_rate_limiter(app):
    """Initialize rate limiter with Flask app"""
    # Default rate limits (per minute)
    default_limit = os.environ.get('RATE_LIMIT_DEFAULT', '100 per minute')
    
    limiter = Limiter(
        app=app,
        key_func=get_rate_limit_key,
        default_limits=[default_limit],
        storage_uri=os.environ.get('RATE_LIMIT_STORAGE_URI'),  # Optional: Redis URL for distributed rate limiting
        headers_enabled=True  # Include rate limit headers in response
    )
    
    return limiter

# Rate limit presets for different endpoint types
RATE_LIMITS = {
    'strict': '10 per minute',      # For sensitive operations (payments, webhooks)
    'moderate': '30 per minute',    # For write operations (create, update, delete)
    'standard': '60 per minute',    # For read operations (GET requests)
    'generous': '100 per minute',   # For less critical endpoints
}

