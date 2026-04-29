"""
GitHub OAuth service for founder verification.

Mirrors the LinkedIn pattern (services/linkedin_service.py). Pulls richer
signals than LinkedIn since GitHub data is fully public:
  - public_repos count
  - followers
  - account creation date (proxy for GitHub seniority)
  - top languages from the user's repos
  - total stars across owned repos

These signals power technical credibility badges, e.g.:
  "10+ public repos / 50+ followers / 6yr GitHub account"
"""
import os
import secrets
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlencode

import requests

from config.database import get_supabase
from utils.logger import log_info, log_error, log_warning


# GitHub OAuth Configuration
GITHUB_CLIENT_ID = os.getenv('GITHUB_CLIENT_ID', '')
GITHUB_CLIENT_SECRET = os.getenv('GITHUB_CLIENT_SECRET', '')
GITHUB_REDIRECT_URI = os.getenv('GITHUB_REDIRECT_URI', '')

# GitHub OAuth URLs
GITHUB_AUTH_URL = 'https://github.com/login/oauth/authorize'
GITHUB_TOKEN_URL = 'https://github.com/login/oauth/access_token'
GITHUB_USER_URL = 'https://api.github.com/user'
GITHUB_REPOS_URL = 'https://api.github.com/user/repos'

# read:user gives us the basic profile; public_repo lets us list owned repos
GITHUB_SCOPE = 'read:user'

# Cap repo enumeration so we don't make unbounded calls for users with thousands of repos
GITHUB_REPO_FETCH_LIMIT = 100


def is_github_configured() -> bool:
    """Check if GitHub OAuth is properly configured."""
    return bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET and GITHUB_REDIRECT_URI)


def _get_founder_id(clerk_user_id: str) -> Optional[str]:
    """Get founder ID from clerk user ID."""
    supabase = get_supabase()
    result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if result.data:
        return result.data[0]['id']
    return None


def generate_oauth_state(clerk_user_id: str) -> str:
    """Generate and persist a one-time OAuth state token for the GitHub flow."""
    state = secrets.token_urlsafe(32)
    supabase = get_supabase()

    try:
        supabase.table('oauth_states').delete().eq(
            'clerk_user_id', clerk_user_id
        ).eq('provider', 'github_founder').execute()
    except Exception:
        pass

    try:
        supabase.table('oauth_states').insert({
            'state': state,
            'clerk_user_id': clerk_user_id,
            'provider': 'github_founder',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'expires_at': datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        log_warning(f"Could not store GitHub OAuth state: {e}")

    return state


def verify_oauth_state(state: str) -> Optional[str]:
    """Verify a state token and return the associated clerk_user_id (one-shot)."""
    supabase = get_supabase()
    try:
        result = supabase.table('oauth_states').select('clerk_user_id').eq('state', state).execute()
        if result.data:
            clerk_user_id = result.data[0]['clerk_user_id']
            supabase.table('oauth_states').delete().eq('state', state).execute()
            return clerk_user_id
    except Exception as e:
        log_warning(f"Could not verify GitHub OAuth state: {e}")
    return None


def get_github_auth_url(clerk_user_id: str) -> Tuple[str, str]:
    """Generate GitHub OAuth authorization URL."""
    if not is_github_configured():
        raise ValueError(
            "GitHub OAuth is not configured. Please set GITHUB_CLIENT_ID, "
            "GITHUB_CLIENT_SECRET, and GITHUB_REDIRECT_URI environment variables."
        )

    state = generate_oauth_state(clerk_user_id)
    params = {
        'client_id': GITHUB_CLIENT_ID,
        'redirect_uri': GITHUB_REDIRECT_URI,
        'state': state,
        'scope': GITHUB_SCOPE,
        'allow_signup': 'true',
    }
    return f"{GITHUB_AUTH_URL}?{urlencode(params)}", state


def exchange_code_for_token(code: str) -> Dict[str, Any]:
    """Exchange a GitHub authorization code for an access token."""
    if not is_github_configured():
        raise ValueError("GitHub OAuth is not configured")

    response = requests.post(
        GITHUB_TOKEN_URL,
        data={
            'client_id': GITHUB_CLIENT_ID,
            'client_secret': GITHUB_CLIENT_SECRET,
            'code': code,
            'redirect_uri': GITHUB_REDIRECT_URI,
        },
        headers={'Accept': 'application/json'},
        timeout=30,
    )

    if response.status_code != 200:
        log_error(f"GitHub token exchange failed: {response.text}")
        raise ValueError(f"Failed to exchange code for token: {response.text}")

    payload = response.json()
    if 'error' in payload:
        # GitHub returns 200 even on errors; the error key signals failure
        raise ValueError(f"GitHub OAuth error: {payload.get('error_description') or payload['error']}")

    return payload


def get_github_profile(access_token: str) -> Dict[str, Any]:
    """Fetch the authenticated user's GitHub profile."""
    response = requests.get(
        GITHUB_USER_URL,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/vnd.github+json',
        },
        timeout=30,
    )
    if response.status_code != 200:
        log_error(f"GitHub profile fetch failed: {response.text}")
        raise ValueError(f"Failed to fetch GitHub profile: {response.text}")
    return response.json()


def _aggregate_repo_stats(access_token: str) -> Dict[str, Any]:
    """
    Compute aggregate stats across the user's owned repos.
    
    GitHub paginates at 100/page. We cap at GITHUB_REPO_FETCH_LIMIT to keep
    the OAuth callback responsive even for users with hundreds of repos.
    """
    languages: Dict[str, int] = {}
    total_stars = 0
    repos_seen = 0

    try:
        response = requests.get(
            GITHUB_REPOS_URL,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/vnd.github+json',
            },
            params={
                'visibility': 'public',
                'affiliation': 'owner',
                'per_page': GITHUB_REPO_FETCH_LIMIT,
                'sort': 'updated',
            },
            timeout=30,
        )

        if response.status_code != 200:
            log_warning(f"GitHub repos fetch returned {response.status_code}; skipping aggregates")
            return {'top_languages': [], 'total_stars': 0, 'repos_analyzed': 0}

        for repo in response.json():
            repos_seen += 1
            total_stars += repo.get('stargazers_count', 0) or 0
            lang = repo.get('language')
            if lang:
                languages[lang] = languages.get(lang, 0) + 1
    except Exception as e:
        log_warning(f"Error aggregating GitHub repo stats: {e}")
        return {'top_languages': [], 'total_stars': 0, 'repos_analyzed': 0}

    top_languages = sorted(languages.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        'top_languages': [name for name, _ in top_languages],
        'total_stars': total_stars,
        'repos_analyzed': repos_seen,
    }


def _account_age_years(created_at_iso: Optional[str]) -> Optional[float]:
    if not created_at_iso:
        return None
    try:
        created = datetime.fromisoformat(created_at_iso.replace('Z', '+00:00'))
        delta = datetime.now(timezone.utc) - created
        return round(delta.days / 365.25, 1)
    except Exception:
        return None


def verify_founder_github(clerk_user_id: str, code: str) -> Dict[str, Any]:
    """
    Complete GitHub verification for a founder.
    
    Stores a snapshot of relevant signals (repo count, followers, top languages,
    account age, total stars) on the `founders.github_data` JSONB column.
    """
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        raise ValueError("User not found")

    token_data = exchange_code_for_token(code)
    access_token = token_data.get('access_token')

    if not access_token:
        raise ValueError("No access token received from GitHub")

    profile = get_github_profile(access_token)
    repo_stats = _aggregate_repo_stats(access_token)

    github_data = {
        'id': profile.get('id'),
        'login': profile.get('login'),
        'name': profile.get('name'),
        'email': profile.get('email'),
        'avatar_url': profile.get('avatar_url'),
        'bio': profile.get('bio'),
        'company': profile.get('company'),
        'location': profile.get('location'),
        'blog': profile.get('blog'),
        'public_repos': profile.get('public_repos', 0),
        'followers': profile.get('followers', 0),
        'following': profile.get('following', 0),
        'created_at': profile.get('created_at'),
        'account_age_years': _account_age_years(profile.get('created_at')),
        'top_languages': repo_stats['top_languages'],
        'total_stars': repo_stats['total_stars'],
        'repos_analyzed': repo_stats['repos_analyzed'],
        'verified_at': datetime.now(timezone.utc).isoformat(),
    }

    supabase = get_supabase()
    result = supabase.table('founders').update({
        'github_verified': True,
        'github_verified_at': datetime.now(timezone.utc).isoformat(),
        'github_data': github_data,
        # Backfill the existing free-text github_url for legacy reads
        'github_url': f"https://github.com/{profile.get('login')}" if profile.get('login') else None,
    }).eq('id', founder_id).execute()

    if not result.data:
        raise ValueError("Failed to update founder profile")

    log_info(f"Founder {founder_id} GitHub verified successfully (login={profile.get('login')})")

    # Activation: GITHUB_VERIFIED (idempotent)
    try:
        from services import activation_service
        activation_service.record_milestone(
            founder_id, activation_service.Milestone.GITHUB_VERIFIED,
            {'github_login': github_data.get('login')},
        )
    except Exception:
        pass

    return {
        'success': True,
        'github_verified': True,
        'github_login': github_data.get('login'),
        'github_name': github_data.get('name'),
        'public_repos': github_data.get('public_repos'),
        'followers': github_data.get('followers'),
        'account_age_years': github_data.get('account_age_years'),
        'top_languages': github_data.get('top_languages'),
        'total_stars': github_data.get('total_stars'),
    }


def get_founder_github_status(clerk_user_id: str) -> Dict[str, Any]:
    """Get GitHub verification status for a founder."""
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        return {'github_verified': False, 'github_configured': is_github_configured()}

    supabase = get_supabase()
    try:
        result = supabase.table('founders').select(
            'github_verified, github_verified_at, github_data'
        ).eq('id', founder_id).execute()

        if result.data:
            profile = result.data[0]
            data = profile.get('github_data') or {}
            return {
                'github_verified': profile.get('github_verified', False),
                'github_verified_at': profile.get('github_verified_at'),
                'github_login': data.get('login'),
                'github_name': data.get('name'),
                'public_repos': data.get('public_repos'),
                'followers': data.get('followers'),
                'account_age_years': data.get('account_age_years'),
                'top_languages': data.get('top_languages'),
                'total_stars': data.get('total_stars'),
                'github_configured': is_github_configured(),
            }
    except Exception as e:
        log_warning(f"Error getting founder GitHub status: {e}")

    return {'github_verified': False, 'github_configured': is_github_configured()}


def revoke_founder_github(clerk_user_id: str) -> Dict[str, Any]:
    """Revoke GitHub verification for a founder."""
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        raise ValueError("User not found")

    supabase = get_supabase()
    result = supabase.table('founders').update({
        'github_verified': False,
        'github_verified_at': None,
        'github_data': None,
    }).eq('id', founder_id).execute()

    if not result.data:
        raise ValueError("Failed to update founder profile")

    log_info(f"Founder {founder_id} GitHub verification revoked")
    return {'success': True, 'github_verified': False}
