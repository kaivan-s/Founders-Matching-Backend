"""Authentication utilities"""
import os
import requests
from flask import request
from utils.logger import log_error, log_warning

# Clerk API configuration
CLERK_SECRET_KEY = os.getenv('CLERK_SECRET_KEY')
CLERK_API_URL = os.getenv('CLERK_API_URL', 'https://api.clerk.com/v1')

def verify_clerk_user_id(clerk_user_id):
    """
    Verify Clerk user ID server-side using Clerk API
    
    Args:
        clerk_user_id: The Clerk user ID to verify
        
    Returns:
        bool: True if user ID is valid, False otherwise
    """
    if not CLERK_SECRET_KEY:
        # In development, allow skipping verification if secret key not set
        # Log warning but don't fail
        log_warning("CLERK_SECRET_KEY not set - skipping user ID verification")
        return True
    
    if not clerk_user_id:
        return False
    
    try:
        # Verify user exists via Clerk API
        headers = {
            'Authorization': f'Bearer {CLERK_SECRET_KEY}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(
            f'{CLERK_API_URL}/users/{clerk_user_id}',
            headers=headers,
            timeout=5
        )
        
        if response.status_code == 200:
            return True
        elif response.status_code == 404:
            log_warning(f"Invalid Clerk user ID: {clerk_user_id}")
            return False
        else:
            # Log error but don't fail authentication (fail open for availability)
            # In production, you might want to fail closed
            log_error(f"Clerk API error verifying user {clerk_user_id}: {response.status_code}")
            return True  # Fail open for now - adjust based on security requirements
    except requests.RequestException as e:
        # Network error - log but don't fail authentication (fail open)
        # In production, you might want to fail closed
        log_error(f"Error verifying Clerk user ID: {str(e)}")
        return True  # Fail open for availability - adjust based on security requirements

def get_clerk_user_id():
    """
    Extract and verify Clerk user ID from request headers
    
    Returns:
        str: Verified Clerk user ID, or None if invalid/missing
    """
    clerk_user_id = request.headers.get('X-Clerk-User-Id') or request.headers.get('x-clerk-user-id')
    
    if not clerk_user_id:
        return None
    
    # Verify user ID server-side to prevent spoofing
    if verify_clerk_user_id(clerk_user_id):
        return clerk_user_id
    else:
        log_warning(f"Failed to verify Clerk user ID: {clerk_user_id}")
        return None

def get_clerk_user_email(clerk_user_id):
    """
    Get user's email from Clerk API
    
    Args:
        clerk_user_id: The Clerk user ID
        
    Returns:
        str: User's email address, or None if not found
    """
    if not CLERK_SECRET_KEY:
        return None
    
    if not clerk_user_id:
        return None
    
    try:
        headers = {
            'Authorization': f'Bearer {CLERK_SECRET_KEY}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(
            f'{CLERK_API_URL}/users/{clerk_user_id}',
            headers=headers,
            timeout=5
        )
        
        if response.status_code == 200:
            user_data = response.json()
            # Clerk returns email_addresses array, get primary email
            email_addresses = user_data.get('email_addresses', [])
            for email_obj in email_addresses:
                if email_obj.get('id') == user_data.get('primary_email_address_id'):
                    return email_obj.get('email_address')
            # Fallback to first email if primary not found
            if email_addresses:
                return email_addresses[0].get('email_address')
        return None
    except Exception as e:
        log_error(f"Error fetching email from Clerk for user {clerk_user_id}: {str(e)}")
        return None

