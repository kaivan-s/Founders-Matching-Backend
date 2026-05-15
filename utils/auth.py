"""Authentication utilities"""
from flask import request
import os
import requests


def get_clerk_user_id():
    """Extract Clerk user ID from request headers"""
    return request.headers.get('X-Clerk-User-Id')


def _fetch_clerk_user(clerk_user_id: str):
    """Fetch user data from Clerk API"""
    clerk_secret_key = os.getenv('CLERK_SECRET_KEY')
    if not clerk_secret_key or not clerk_user_id:
        return None
    
    try:
        headers = {
            'Authorization': f'Bearer {clerk_secret_key}',
            'Content-Type': 'application/json'
        }
        response = requests.get(
            f'https://api.clerk.com/v1/users/{clerk_user_id}',
            headers=headers,
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def get_clerk_user_email(clerk_user_id: str = None):
    """
    Extract Clerk user email from request headers or fetch from Clerk API
    
    Args:
        clerk_user_id: Optional Clerk user ID. If provided and email not in headers, 
                      will attempt to fetch from Clerk API
    
    Returns:
        str: User email or None if not found
    """
    # First try to get from headers (most common case)
    email = request.headers.get('X-User-Email')
    if email:
        return email.strip()
    
    # If not in headers and clerk_user_id provided, try Clerk API
    if clerk_user_id:
        user_data = _fetch_clerk_user(clerk_user_id)
        if user_data:
            return user_data.get('email_addresses', [{}])[0].get('email_address')
    
    return None


def get_clerk_user_name(clerk_user_id: str = None):
    """
    Extract Clerk user name from request headers.
    
    Returns:
        str: User full name or None if not found
    """
    name = request.headers.get('X-User-Name')
    if name and name.strip():
        return name.strip()
    return None
