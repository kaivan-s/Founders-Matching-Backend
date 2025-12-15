"""Authentication utilities"""
from flask import request

def get_clerk_user_id():
    """Extract Clerk user ID from request headers"""
    return request.headers.get('X-Clerk-User-Id') or request.headers.get('x-clerk-user-id')

