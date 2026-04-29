"""
Verification level computation for founders.

Combines signals from LinkedIn (linkedin_data) and GitHub (github_data) into
a single verification tier the frontend can render as a badge and discovery
can filter on.

Tiers (lowest to highest):
  UNVERIFIED      - email only
  VERIFIED        - LinkedIn linked
  PRO_VERIFIED    - LinkedIn + LinkedIn email matches founder email (anti-impersonation)
  HIGHLY_VERIFIED - LinkedIn + GitHub (or future: LinkedIn + Persona ID)
"""
from typing import Dict, Any, Optional
from config.database import get_supabase
from utils.logger import log_warning


VERIFICATION_TIERS = {
    'UNVERIFIED': {
        'level': 0,
        'label': 'Unverified',
        'badge': None,
    },
    'VERIFIED': {
        'level': 1,
        'label': 'Verified',
        'badge': 'blue_check',
    },
    'PRO_VERIFIED': {
        'level': 2,
        'label': 'Pro Verified',
        'badge': 'blue_check_plus',
    },
    'HIGHLY_VERIFIED': {
        'level': 3,
        'label': 'Highly Verified',
        'badge': 'gold_check',
    },
}


def _emails_match(linkedin_email: Optional[str], founder_email: Optional[str]) -> bool:
    """
    Return True if LinkedIn-verified email matches the founder's signup email.
    Compares case-insensitively and trims whitespace.
    """
    if not linkedin_email or not founder_email:
        return False
    return linkedin_email.strip().lower() == founder_email.strip().lower()


def compute_verification_tier(founder_row: Dict[str, Any]) -> str:
    """
    Pure function: takes a founder DB row and returns a tier name.
    
    Args:
        founder_row: dict with at least linkedin_verified, github_verified,
                     linkedin_data, email
        
    Returns:
        One of 'UNVERIFIED', 'VERIFIED', 'PRO_VERIFIED', 'HIGHLY_VERIFIED'
    """
    linkedin_ok = bool(founder_row.get('linkedin_verified'))
    github_ok = bool(founder_row.get('github_verified'))

    if not linkedin_ok and not github_ok:
        return 'UNVERIFIED'

    if linkedin_ok and github_ok:
        return 'HIGHLY_VERIFIED'

    if linkedin_ok:
        linkedin_data = founder_row.get('linkedin_data') or {}
        linkedin_email = linkedin_data.get('email')
        linkedin_email_verified = linkedin_data.get('email_verified', False)
        founder_email = founder_row.get('email')

        if linkedin_email_verified and _emails_match(linkedin_email, founder_email):
            return 'PRO_VERIFIED'
        return 'VERIFIED'

    # GitHub-only verification still counts as VERIFIED (lowest paid tier)
    if github_ok:
        return 'VERIFIED'

    return 'UNVERIFIED'


def get_verification_status(clerk_user_id: str) -> Dict[str, Any]:
    """
    Aggregate verification status for a founder. Single endpoint the frontend
    can hit to render the entire verification UI.
    """
    supabase = get_supabase()

    try:
        result = supabase.table('founders').select(
            'id, email, '
            'linkedin_verified, linkedin_verified_at, linkedin_data, '
            'github_verified, github_verified_at, github_data'
        ).eq('clerk_user_id', clerk_user_id).execute()
    except Exception as e:
        log_warning(f"Error fetching verification status: {e}")
        return _empty_status()

    if not result.data:
        return _empty_status()

    row = result.data[0]
    tier_name = compute_verification_tier(row)
    tier_info = VERIFICATION_TIERS[tier_name]

    linkedin_data = row.get('linkedin_data') or {}
    github_data = row.get('github_data') or {}

    return {
        'tier': tier_name,
        'tier_level': tier_info['level'],
        'tier_label': tier_info['label'],
        'tier_badge': tier_info['badge'],
        'linkedin': {
            'verified': bool(row.get('linkedin_verified')),
            'verified_at': row.get('linkedin_verified_at'),
            'name': linkedin_data.get('name'),
            'email': linkedin_data.get('email'),
            'email_verified': linkedin_data.get('email_verified'),
            'picture': linkedin_data.get('picture'),
            'email_matches_account': _emails_match(
                linkedin_data.get('email'), row.get('email')
            ) if linkedin_data else False,
        },
        'github': {
            'verified': bool(row.get('github_verified')),
            'verified_at': row.get('github_verified_at'),
            'login': github_data.get('login'),
            'public_repos': github_data.get('public_repos'),
            'followers': github_data.get('followers'),
            'account_age_years': github_data.get('account_age_years'),
            'top_languages': github_data.get('top_languages'),
            'total_stars': github_data.get('total_stars'),
        },
        'next_tier': _next_tier_hint(tier_name),
    }


def _empty_status() -> Dict[str, Any]:
    tier_info = VERIFICATION_TIERS['UNVERIFIED']
    return {
        'tier': 'UNVERIFIED',
        'tier_level': 0,
        'tier_label': tier_info['label'],
        'tier_badge': None,
        'linkedin': {'verified': False},
        'github': {'verified': False},
        'next_tier': _next_tier_hint('UNVERIFIED'),
    }


def _next_tier_hint(current_tier: str) -> Optional[Dict[str, str]]:
    """Tell the frontend what the user needs to do to reach the next tier."""
    hints = {
        'UNVERIFIED': {
            'tier': 'VERIFIED',
            'action': 'connect_linkedin',
            'message': 'Connect LinkedIn to get a Verified badge.',
        },
        'VERIFIED': {
            'tier': 'PRO_VERIFIED',
            'action': 'match_emails_or_connect_github',
            'message': 'Connect GitHub or use the same email as your LinkedIn to upgrade.',
        },
        'PRO_VERIFIED': {
            'tier': 'HIGHLY_VERIFIED',
            'action': 'connect_github',
            'message': 'Connect GitHub for the Highly Verified gold badge.',
        },
        'HIGHLY_VERIFIED': None,
    }
    return hints.get(current_tier)
