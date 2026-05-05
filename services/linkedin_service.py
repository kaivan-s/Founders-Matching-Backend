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


def generate_oauth_state(clerk_user_id: str, role: str = 'advisor') -> str:
    """
    Generate a secure state token for OAuth flow.
    Stores the state in database for verification during callback.
    
    The state token is prefixed with the role (e.g., 'advisor_xxx' or 'founder_xxx')
    so that even if DB lookup fails, we can still determine the role.
    
    Args:
        clerk_user_id: The Clerk user ID initiating the OAuth flow
        role: 'advisor' or 'founder' - determines which table the callback updates
        
    Returns:
        The generated state token (format: {role}_{random_token})
    """
    random_part = secrets.token_urlsafe(32)
    # Prefix state with role so we can extract it even if DB lookup fails
    state = f"{role}_{random_part}"
    
    # Store state in database with expiry (15 minutes)
    supabase = get_supabase()
    
    # Clean up old states for this user (same provider) first
    try:
        supabase.table('oauth_states').delete().eq('clerk_user_id', clerk_user_id).eq('provider', f'linkedin_{role}').execute()
    except Exception:
        pass  # Table might not exist yet, will be created on insert
    
    try:
        supabase.table('oauth_states').insert({
            'state': state,
            'clerk_user_id': clerk_user_id,
            'provider': f'linkedin_{role}',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'expires_at': datetime.now(timezone.utc).isoformat(),  # Will add 15 min in SQL
        }).execute()
    except Exception as e:
        log_warning(f"Could not store OAuth state (table may not exist): {e}")
        # Continue anyway - role is encoded in state token itself
    
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


def extract_role_from_state(state: str) -> str:
    """
    Extract role from state token prefix.
    State format is '{role}_{random_token}' (e.g., 'advisor_abc123' or 'founder_xyz789')
    
    Returns:
        'advisor' or 'founder', defaults to 'advisor'
    """
    if state and '_' in state:
        prefix = state.split('_')[0]
        if prefix in ('advisor', 'founder'):
            return prefix
    return 'advisor'  # default


def verify_oauth_state_with_role(state: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Verify an OAuth state token and return both clerk_user_id and role.
    
    The role can be determined from:
    1. The database record (preferred, includes user verification)
    2. The state prefix as fallback (role is encoded in state token)
    
    Args:
        state: The state token to verify (format: {role}_{random_token})
        
    Returns:
        Tuple of (clerk_user_id, role) if valid, (None, role_from_state) if DB lookup fails
        role will be 'advisor' or 'founder'
    """
    # Extract role from state prefix (always available as fallback)
    role_from_state = extract_role_from_state(state)
    
    supabase = get_supabase()
    
    try:
        result = supabase.table('oauth_states').select('clerk_user_id, provider').eq('state', state).execute()
        
        if result.data:
            clerk_user_id = result.data[0]['clerk_user_id']
            provider = result.data[0].get('provider', '')
            # Extract role from provider (e.g., 'linkedin_founder' -> 'founder')
            role = 'advisor'  # default
            if '_founder' in provider:
                role = 'founder'
            elif '_advisor' in provider:
                role = 'advisor'
            # Delete the used state
            supabase.table('oauth_states').delete().eq('state', state).execute()
            return clerk_user_id, role
    except Exception as e:
        log_warning(f"Could not verify OAuth state with role: {e}")
    
    # DB lookup failed, but we can still return the role from state prefix
    return None, role_from_state


def get_linkedin_auth_url(clerk_user_id: str, role: str = 'advisor') -> Tuple[str, str]:
    """
    Generate LinkedIn OAuth authorization URL.
    
    Args:
        clerk_user_id: The Clerk user ID initiating the OAuth flow
        role: 'advisor' or 'founder'
        
    Returns:
        Tuple of (auth_url, state)
    """
    if not is_linkedin_configured():
        raise ValueError("LinkedIn OAuth is not configured. Please set LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, and LINKEDIN_REDIRECT_URI environment variables.")
    
    state = generate_oauth_state(clerk_user_id, role=role)
    
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
    
    # Check if advisor profile exists
    current_profile = supabase.table('advisor_profiles').select('id, verification_badges').eq('user_id', founder_id).execute()
    
    verification_time = datetime.now(timezone.utc).isoformat()
    
    if current_profile.data:
        # Profile exists - update it
        current_badges = current_profile.data[0].get('verification_badges') or []
        if 'linkedin' not in current_badges:
            current_badges.append('linkedin')
        
        result = supabase.table('advisor_profiles').update({
            'linkedin_verified': True,
            'linkedin_verified_at': verification_time,
            'linkedin_data': linkedin_data,
            'verification_badges': current_badges,
        }).eq('user_id', founder_id).execute()
        
        if not result.data:
            raise ValueError("Failed to update advisor profile")
    else:
        # Profile doesn't exist yet - create a minimal one with LinkedIn verification
        # The user will complete the rest during onboarding
        result = supabase.table('advisor_profiles').insert({
            'user_id': founder_id,
            'linkedin_verified': True,
            'linkedin_verified_at': verification_time,
            'linkedin_data': linkedin_data,
            'verification_badges': ['linkedin'],
            'status': 'PENDING',  # Will be submitted when onboarding is complete
            'is_discoverable': False,
            'max_active_workspaces': 3,
        }).execute()
        
        if not result.data:
            raise ValueError("Failed to create advisor profile with LinkedIn verification")
        
        log_info(f"Created new advisor profile for {founder_id} with LinkedIn verification")
    
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
    
    # Get current badges and remove 'linkedin' if present
    current_profile = supabase.table('advisor_profiles').select('verification_badges').eq('user_id', founder_id).execute()
    current_badges = []
    if current_profile.data and current_profile.data[0].get('verification_badges'):
        current_badges = [b for b in current_profile.data[0]['verification_badges'] if b != 'linkedin']
    
    result = supabase.table('advisor_profiles').update({
        'linkedin_verified': False,
        'linkedin_verified_at': None,
        'linkedin_data': None,
        'verification_badges': current_badges,
    }).eq('user_id', founder_id).execute()
    
    if not result.data:
        raise ValueError("Failed to update advisor profile")
    
    log_info(f"Advisor {founder_id} LinkedIn verification revoked")
    
    return {'success': True, 'linkedin_verified': False}


# ============================================================
# FOUNDER-SIDE FUNCTIONS
# Mirrors the advisor flow but writes to the `founders` table
# instead of `advisor_profiles`. The OAuth machinery above is
# shared by passing role='founder' through generate_oauth_state.
# ============================================================

def verify_founder_linkedin(clerk_user_id: str, code: str) -> Dict[str, Any]:
    """
    Complete LinkedIn verification for a founder.
    
    Args:
        clerk_user_id: The Clerk user ID
        code: The authorization code from LinkedIn callback
        
    Returns:
        Updated founder verification status
    """
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        raise ValueError("User not found")
    
    token_data = exchange_code_for_token(code)
    access_token = token_data.get('access_token')
    
    if not access_token:
        raise ValueError("No access token received from LinkedIn")
    
    linkedin_profile = get_linkedin_profile(access_token)
    
    linkedin_data = {
        'sub': linkedin_profile.get('sub'),
        'name': linkedin_profile.get('name'),
        'given_name': linkedin_profile.get('given_name'),
        'family_name': linkedin_profile.get('family_name'),
        'email': linkedin_profile.get('email'),
        'email_verified': linkedin_profile.get('email_verified'),
        'picture': linkedin_profile.get('picture'),
        'locale': linkedin_profile.get('locale'),
        'verified_at': datetime.now(timezone.utc).isoformat(),
    }
    
    supabase = get_supabase()
    
    result = supabase.table('founders').update({
        'linkedin_verified': True,
        'linkedin_verified_at': datetime.now(timezone.utc).isoformat(),
        'linkedin_data': linkedin_data,
    }).eq('id', founder_id).execute()
    
    if not result.data:
        raise ValueError("Failed to update founder profile")
    
    log_info(f"Founder {founder_id} LinkedIn verified successfully")

    # Activation: LINKEDIN_VERIFIED (idempotent)
    try:
        from services import activation_service
        activation_service.record_milestone(
            founder_id, activation_service.Milestone.LINKEDIN_VERIFIED,
            {'linkedin_name': linkedin_data.get('name')},
        )
    except Exception:
        pass

    return {
        'success': True,
        'linkedin_verified': True,
        'linkedin_name': linkedin_data.get('name'),
        'linkedin_email': linkedin_data.get('email'),
        'linkedin_email_verified': linkedin_data.get('email_verified'),
        'linkedin_picture': linkedin_data.get('picture'),
    }


def get_founder_linkedin_status(clerk_user_id: str) -> Dict[str, Any]:
    """Get LinkedIn verification status for a founder."""
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        return {'linkedin_verified': False, 'linkedin_configured': is_linkedin_configured()}
    
    supabase = get_supabase()
    
    try:
        result = supabase.table('founders').select(
            'linkedin_verified, linkedin_verified_at, linkedin_data'
        ).eq('id', founder_id).execute()
        
        if result.data:
            profile = result.data[0]
            data = profile.get('linkedin_data') or {}
            return {
                'linkedin_verified': profile.get('linkedin_verified', False),
                'linkedin_verified_at': profile.get('linkedin_verified_at'),
                'linkedin_name': data.get('name'),
                'linkedin_email': data.get('email'),
                'linkedin_email_verified': data.get('email_verified'),
                'linkedin_picture': data.get('picture'),
                'linkedin_configured': is_linkedin_configured(),
            }
    except Exception as e:
        log_warning(f"Error getting founder LinkedIn status: {e}")
    
    return {'linkedin_verified': False, 'linkedin_configured': is_linkedin_configured()}


def revoke_founder_linkedin(clerk_user_id: str) -> Dict[str, Any]:
    """Revoke LinkedIn verification for a founder."""
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        raise ValueError("User not found")
    
    supabase = get_supabase()
    
    result = supabase.table('founders').update({
        'linkedin_verified': False,
        'linkedin_verified_at': None,
        'linkedin_data': None,
    }).eq('id', founder_id).execute()
    
    if not result.data:
        raise ValueError("Failed to update founder profile")
    
    log_info(f"Founder {founder_id} LinkedIn verification revoked")
    
    return {'success': True, 'linkedin_verified': False}
