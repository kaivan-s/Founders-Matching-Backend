"""Admin service for managing advisor approvals"""
import os
from config.database import get_supabase


def is_admin(clerk_user_id: str) -> bool:
    """Check if user is admin based on ADMIN_CLERK_USER_IDS env var (comma-separated)"""
    if not clerk_user_id:
        return False
    admin_ids = os.getenv('ADMIN_CLERK_USER_IDS', '')
    if not admin_ids.strip():
        return False
    allowed = [x.strip() for x in admin_ids.split(',') if x.strip()]
    return clerk_user_id in allowed


def list_pending_advisors():
    """List all advisor profiles with PENDING status"""
    supabase = get_supabase()
    result = supabase.table('advisor_profiles').select(
        'id, user_id, headline, bio, status, is_discoverable, expertise_stages, domains, '
        'max_active_workspaces, preferred_cadence, timezone, created_at, updated_at, '
        'contact_email, meeting_link, contact_note, linkedin_url, twitter_url, questionnaire_data'
    ).eq('status', 'PENDING').order('created_at', desc=True).execute()

    profiles = result.data or []
    if profiles:
        user_ids = list({p['user_id'] for p in profiles})
        users = supabase.table('founders').select('id, name, email, clerk_user_id').in_('id', user_ids).execute()
        user_map = {u['id']: u for u in (users.data or [])}
        for p in profiles:
            p['user'] = user_map.get(p['user_id'], {})
    return {'advisors': profiles, 'total': len(profiles)}


def get_advisor_by_id(advisor_id: str):
    """Get full advisor profile by advisor_profiles.id for admin review"""
    supabase = get_supabase()
    result = supabase.table('advisor_profiles').select(
        '*, user:founders!user_id(id, name, email, clerk_user_id, created_at)'
    ).eq('id', advisor_id).execute()

    if not result.data or len(result.data) == 0:
        return None
    return result.data[0]


def approve_advisor(advisor_id: str):
    """Set advisor status to APPROVED and is_discoverable to True"""
    supabase = get_supabase()
    result = supabase.table('advisor_profiles').update({
        'status': 'APPROVED',
        'is_discoverable': True,
    }).eq('id', advisor_id).execute()

    if not result.data:
        return None
    return result.data[0]


def reject_advisor(advisor_id: str):
    """Set advisor status to REJECTED"""
    supabase = get_supabase()
    result = supabase.table('advisor_profiles').update({
        'status': 'REJECTED',
    }).eq('id', advisor_id).execute()

    if not result.data:
        return None
    return result.data[0]
