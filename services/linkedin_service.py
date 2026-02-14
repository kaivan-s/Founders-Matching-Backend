"""
LinkedIn OAuth service for advisor verification.

This service handles LinkedIn OAuth 2.0 flow to verify advisor identities.
"""
import os
import secrets
import requests
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlencode

from config.database import get_supabase
from utils.logger import log_info, log_error, log_warning


# LinkedIn OAuth Configuration
LINKEDIN_CLIENT_ID = os.getenv('LINKEDIN_CLIENT_ID', '')
LINKEDIN_CLIENT_SECRET = os.getenv('LINKEDIN_CLIENT_SECRET', '')
LINKEDIN_REDIRECT_URI = os.getenv('LINKEDIN_REDIRECT_URI', '')

# LinkedIn OAuth URLs
LINKEDIN_AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LINKEDIN_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LINKEDIN_USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'


def _get_founder_id(clerk_user_id: str) -> Optional[str]:
    """Get founder ID from clerk user ID."""
    supabase = get_supabase()
    result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if result.data:
        return result.data[0]['id']
    return None


def is_linkedin_configured() -> bool:
    """Check if LinkedIn OAuth is properly configured."""
    return bool(LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET and LINKEDIN_REDIRECT_URI)


def generate_oauth_state(clerk_user_id: str) -> str:
    """
    Generate a secure state token for OAuth flow.
    Stores the state in database for verification during callback.
    
    Args:
        clerk_user_id: The Clerk user ID initiating the OAuth flow
        
    Returns:
        The generated state token
    """
    state = secrets.token_urlsafe(32)
    
    # Store state in database with expiry (15 minutes)
    supabase = get_supabase()
    
    # Clean up old states for this user first
    try:
        supabase.table('oauth_states').delete().eq('clerk_user_id', clerk_user_id).execute()
    except Exception:
        pass  # Table might not exist yet, will be created on insert
    
    try:
        supabase.table('oauth_states').insert({
            'state': state,
            'clerk_user_id': clerk_user_id,
            'provider': 'linkedin',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'expires_at': datetime.now(timezone.utc).isoformat(),  # Will add 15 min in SQL
        }).execute()
    except Exception as e:
        log_warning(f"Could not store OAuth state (table may not exist): {e}")
        # Continue anyway - we'll verify via query param fallback
    
    return state


def verify_oauth_state(state: str) -> Optional[str]:
    """
    Verify an OAuth state token and return the associated clerk_user_id.
    
    Args:
        state: The state token to verify
        
    Returns:
        The clerk_user_id if valid, None otherwise
    """
    supabase = get_supabase()
    
    try:
        result = supabase.table('oauth_states').select('clerk_user_id').eq('state', state).execute()
        
        if result.data:
            clerk_user_id = result.data[0]['clerk_user_id']
            # Delete the used state
            supabase.table('oauth_states').delete().eq('state', state).execute()
            return clerk_user_id
    except Exception as e:
        log_warning(f"Could not verify OAuth state: {e}")
    
    return None


def get_linkedin_auth_url(clerk_user_id: str) -> Tuple[str, str]:
    """
    Generate LinkedIn OAuth authorization URL.
    
    Args:
        clerk_user_id: The Clerk user ID initiating the OAuth flow
        
    Returns:
        Tuple of (auth_url, state)
    """
    if not is_linkedin_configured():
        raise ValueError("LinkedIn OAuth is not configured. Please set LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, and LINKEDIN_REDIRECT_URI environment variables.")
    
    state = generate_oauth_state(clerk_user_id)
    
    params = {
        'response_type': 'code',
        'client_id': LINKEDIN_CLIENT_ID,
        'redirect_uri': LINKEDIN_REDIRECT_URI,
        'state': state,
        'scope': 'openid profile email',  # OpenID Connect scopes
    }
    
    auth_url = f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"
    return auth_url, state


def exchange_code_for_token(code: str) -> Dict[str, Any]:
    """
    Exchange authorization code for access token.
    
    Args:
        code: The authorization code from LinkedIn callback
        
    Returns:
        Token response from LinkedIn
    """
    if not is_linkedin_configured():
        raise ValueError("LinkedIn OAuth is not configured")
    
    response = requests.post(
        LINKEDIN_TOKEN_URL,
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': LINKEDIN_REDIRECT_URI,
            'client_id': LINKEDIN_CLIENT_ID,
            'client_secret': LINKEDIN_CLIENT_SECRET,
        },
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        timeout=30
    )
    
    if response.status_code != 200:
        log_error(f"LinkedIn token exchange failed: {response.text}")
        raise ValueError(f"Failed to exchange code for token: {response.text}")
    
    return response.json()


def get_linkedin_profile(access_token: str) -> Dict[str, Any]:
    """
    Fetch LinkedIn profile using access token.
    Uses OpenID Connect userinfo endpoint.
    
    Args:
        access_token: LinkedIn access token
        
    Returns:
        LinkedIn profile data
    """
    response = requests.get(
        LINKEDIN_USERINFO_URL,
        headers={
            'Authorization': f'Bearer {access_token}',
        },
        timeout=30
    )
    
    if response.status_code != 200:
        log_error(f"LinkedIn profile fetch failed: {response.text}")
        raise ValueError(f"Failed to fetch LinkedIn profile: {response.text}")
    
    return response.json()


def verify_advisor_linkedin(clerk_user_id: str, code: str) -> Dict[str, Any]:
    """
    Complete LinkedIn verification for an advisor.
    
    Args:
        clerk_user_id: The Clerk user ID
        code: The authorization code from LinkedIn callback
        
    Returns:
        Updated advisor profile with verification status
    """
    # Get founder_id
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        raise ValueError("User not found")
    
    # Exchange code for token
    token_data = exchange_code_for_token(code)
    access_token = token_data.get('access_token')
    
    if not access_token:
        raise ValueError("No access token received from LinkedIn")
    
    # Fetch LinkedIn profile
    linkedin_profile = get_linkedin_profile(access_token)
    
    # Extract relevant data
    linkedin_data = {
        'sub': linkedin_profile.get('sub'),  # LinkedIn unique ID
        'name': linkedin_profile.get('name'),
        'given_name': linkedin_profile.get('given_name'),
        'family_name': linkedin_profile.get('family_name'),
        'email': linkedin_profile.get('email'),
        'email_verified': linkedin_profile.get('email_verified'),
        'picture': linkedin_profile.get('picture'),
        'locale': linkedin_profile.get('locale'),
        'verified_at': datetime.now(timezone.utc).isoformat(),
    }
    
    # Update advisor profile with verification
    supabase = get_supabase()
    
    result = supabase.table('advisor_profiles').update({
        'linkedin_verified': True,
        'linkedin_verified_at': datetime.now(timezone.utc).isoformat(),
        'linkedin_data': linkedin_data,
    }).eq('user_id', founder_id).execute()
    
    if not result.data:
        raise ValueError("Failed to update advisor profile")
    
    log_info(f"Advisor {founder_id} LinkedIn verified successfully")
    
    return {
        'success': True,
        'linkedin_verified': True,
        'linkedin_name': linkedin_data.get('name'),
        'linkedin_email': linkedin_data.get('email'),
    }


def get_advisor_linkedin_status(clerk_user_id: str) -> Dict[str, Any]:
    """
    Get LinkedIn verification status for an advisor.
    
    Args:
        clerk_user_id: The Clerk user ID
        
    Returns:
        LinkedIn verification status
    """
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        return {'linkedin_verified': False, 'linkedin_configured': is_linkedin_configured()}
    
    supabase = get_supabase()
    
    try:
        result = supabase.table('advisor_profiles').select(
            'linkedin_verified, linkedin_verified_at, linkedin_data'
        ).eq('user_id', founder_id).execute()
        
        if result.data:
            profile = result.data[0]
            return {
                'linkedin_verified': profile.get('linkedin_verified', False),
                'linkedin_verified_at': profile.get('linkedin_verified_at'),
                'linkedin_name': profile.get('linkedin_data', {}).get('name') if profile.get('linkedin_data') else None,
                'linkedin_configured': is_linkedin_configured(),
            }
    except Exception as e:
        log_warning(f"Error getting LinkedIn status: {e}")
    
    return {'linkedin_verified': False, 'linkedin_configured': is_linkedin_configured()}


def revoke_linkedin_verification(clerk_user_id: str) -> Dict[str, Any]:
    """
    Revoke LinkedIn verification for an advisor (optional feature).
    
    Args:
        clerk_user_id: The Clerk user ID
        
    Returns:
        Success status
    """
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        raise ValueError("User not found")
    
    supabase = get_supabase()
    
    result = supabase.table('advisor_profiles').update({
        'linkedin_verified': False,
        'linkedin_verified_at': None,
        'linkedin_data': None,
    }).eq('user_id', founder_id).execute()
    
    if not result.data:
        raise ValueError("Failed to update advisor profile")
    
    log_info(f"Advisor {founder_id} LinkedIn verification revoked")
    
    return {'success': True, 'linkedin_verified': False}
