"""Authentication utilities"""
from flask import request
import os
import requests


def get_clerk_user_id():
    """Extract Clerk user ID from request headers"""
    return request.headers.get('X-Clerk-User-Id')


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
        clerk_secret_key = os.getenv('CLERK_SECRET_KEY')
        if clerk_secret_key:
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
                    user_data = response.json()
                    return user_data.get('email_addresses', [{}])[0].get('email_address')
            except Exception:
                pass
    
    return None
