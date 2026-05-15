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
        'preferred_stages, advisory_types, max_active_workspaces, preferred_cadence, timezone, '
        'created_at, updated_at, contact_email, meeting_link, contact_note, linkedin_url, twitter_url, '
        'questionnaire_data, professional_background, portfolio, verification_badges, '
        'profile_completion_score, linkedin_verified, linkedin_verified_at, '
        'consultation_rate_30min_usd, consultation_rate_60min_usd, availability_hours_per_week, '
        'profile_image_url'
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
    """Set advisor status to APPROVED and is_discoverable to True, and send email"""
    supabase = get_supabase()
    
    # Get advisor profile with user info for email
    advisor = supabase.table('advisor_profiles').select(
        'id, user_id, contact_email'
    ).eq('id', advisor_id).execute()
    
    if not advisor.data:
        return None
    
    # Update status
    result = supabase.table('advisor_profiles').update({
        'status': 'APPROVED',
        'is_discoverable': True,
    }).eq('id', advisor_id).execute()

    if not result.data:
        return None
    
    # Get user's name and email for notification
    try:
        user_id = advisor.data[0].get('user_id')
        contact_email = advisor.data[0].get('contact_email')
        
        user = supabase.table('founders').select('name, email').eq('id', user_id).execute()
        user_name = user.data[0].get('name', 'there') if user.data else 'there'
        user_email = contact_email or (user.data[0].get('email') if user.data else None)
        
        if user_email:
            from services import email_service
            email_service.send_advisor_approved_email(user_email, user_name.split()[0] if user_name else 'there')
    except Exception as e:
        # Log but don't fail if email fails
        print(f"[ADMIN] Failed to send advisor approval email: {e}")
    
    return result.data[0]


def reject_advisor(advisor_id: str, reason: str = None):
    """Set advisor status to REJECTED and send email"""
    supabase = get_supabase()
    
    # Get advisor profile with user info for email
    advisor = supabase.table('advisor_profiles').select(
        'id, user_id, contact_email'
    ).eq('id', advisor_id).execute()
    
    if not advisor.data:
        return None
    
    # Update status
    result = supabase.table('advisor_profiles').update({
        'status': 'REJECTED',
    }).eq('id', advisor_id).execute()

    if not result.data:
        return None
    
    # Get user's name and email for notification
    try:
        user_id = advisor.data[0].get('user_id')
        contact_email = advisor.data[0].get('contact_email')
        
        user = supabase.table('founders').select('name, email').eq('id', user_id).execute()
        user_name = user.data[0].get('name', 'there') if user.data else 'there'
        user_email = contact_email or (user.data[0].get('email') if user.data else None)
        
        if user_email:
            from services import email_service
            email_service.send_advisor_rejected_email(user_email, user_name.split()[0] if user_name else 'there', reason)
    except Exception as e:
        # Log but don't fail if email fails
        print(f"[ADMIN] Failed to send advisor rejection email: {e}")
    
    return result.data[0]
