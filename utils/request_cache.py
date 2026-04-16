"""
Request-scoped cache for reducing redundant database queries.
This cache is cleared at the end of each request.
"""
from threading import local
from typing import Any, Optional, Dict
from functools import wraps

# Thread-local storage for request-scoped data
_request_local = local()


def get_cache() -> Dict[str, Any]:
    """Get the request-scoped cache dictionary"""
    if not hasattr(_request_local, 'cache'):
        _request_local.cache = {}
    return _request_local.cache


def clear_cache():
    """Clear the request-scoped cache. Call at end of each request."""
    if hasattr(_request_local, 'cache'):
        _request_local.cache = {}


def cache_get(key: str) -> Optional[Any]:
    """Get a value from the request cache"""
    return get_cache().get(key)


def cache_set(key: str, value: Any) -> None:
    """Set a value in the request cache"""
    get_cache()[key] = value


def cache_delete(key: str) -> None:
    """Delete a value from the request cache"""
    cache = get_cache()
    if key in cache:
        del cache[key]


# Cached founder data accessors
def get_cached_founder_id(clerk_user_id: str) -> Optional[str]:
    """Get cached founder_id for a clerk_user_id"""
    return cache_get(f'founder_id:{clerk_user_id}')


def set_cached_founder_id(clerk_user_id: str, founder_id: str) -> None:
    """Cache founder_id for a clerk_user_id"""
    cache_set(f'founder_id:{clerk_user_id}', founder_id)


def get_cached_founder_data(clerk_user_id: str) -> Optional[Dict]:
    """Get cached full founder data for a clerk_user_id"""
    return cache_get(f'founder_data:{clerk_user_id}')


def set_cached_founder_data(clerk_user_id: str, data: Dict) -> None:
    """Cache full founder data for a clerk_user_id"""
    cache_set(f'founder_data:{clerk_user_id}', data)


def get_cached_plan(clerk_user_id: str) -> Optional[Dict]:
    """Get cached plan for a clerk_user_id"""
    return cache_get(f'plan:{clerk_user_id}')


def set_cached_plan(clerk_user_id: str, plan: Dict) -> None:
    """Cache plan for a clerk_user_id"""
    cache_set(f'plan:{clerk_user_id}', plan)


def cached(key_prefix: str):
    """
    Decorator for caching function results.
    The first argument of the decorated function is used as the cache key suffix.
    
    Usage:
        @cached('founder_id')
        def get_founder_id(clerk_user_id: str) -> str:
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if args:
                cache_key = f'{key_prefix}:{args[0]}'
                cached_value = cache_get(cache_key)
                if cached_value is not None:
                    return cached_value
                
                result = func(*args, **kwargs)
                if result is not None:
                    cache_set(cache_key, result)
                return result
            return func(*args, **kwargs)
        return wrapper
    return decorator
