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


def _is_profile_complete(profile: dict) -> bool:
    """Check if advisor profile has all required fields completed"""
    # Required fields matching frontend getProfileCompletion()
    headline = (profile.get('headline') or '').strip()
    bio = profile.get('bio') or ''
    linkedin_url = (profile.get('linkedin_url') or '').strip()
    advisory_types = profile.get('advisory_types') or []
    preferred_stages = profile.get('preferred_stages') or []
    domains = profile.get('domains') or []
    availability = profile.get('availability_hours_per_week')
    
    return all([
        bool(headline),
        len(bio) >= 100,
        bool(linkedin_url),
        len(advisory_types) > 0,
        len(preferred_stages) > 0,
        len(domains) > 0,
        availability is not None,
    ])


def list_pending_advisors():
    """List advisor profiles with PENDING status that have complete profiles"""
    supabase = get_supabase()
    result = supabase.table('advisor_profiles').select(
        'id, clerk_user_id, email, name, headline, bio, status, is_discoverable, expertise_stages, domains, '
        'preferred_stages, advisory_types, max_active_workspaces, preferred_cadence, timezone, '
        'created_at, updated_at, contact_email, meeting_link, contact_note, linkedin_url, twitter_url, '
        'questionnaire_data, professional_background, portfolio, verification_badges, '
        'profile_completion_score, linkedin_verified, linkedin_verified_at, '
        'consultation_rate_30min_usd, consultation_rate_60min_usd, availability_hours_per_week, '
        'profile_image_url'
    ).eq('status', 'PENDING').order('created_at', desc=True).execute()

    all_pending = result.data or []
    
    # Filter to only show profiles that are complete
    complete_profiles = [p for p in all_pending if _is_profile_complete(p)]
    incomplete_count = len(all_pending) - len(complete_profiles)
    
    return {
        'advisors': complete_profiles, 
        'total': len(complete_profiles),
        'incomplete_count': incomplete_count,  # For admin awareness
    }


def get_advisor_by_id(advisor_id: str):
    """Get full advisor profile by advisor_profiles.id for admin review"""
    supabase = get_supabase()
    result = supabase.table('advisor_profiles').select('*').eq('id', advisor_id).execute()

    if not result.data or len(result.data) == 0:
        return None
    return result.data[0]


def approve_advisor(advisor_id: str):
    """Set advisor status to APPROVED and is_discoverable to True, and send email"""
    supabase = get_supabase()
    
    # Get advisor profile with name and contact info for email
    advisor = supabase.table('advisor_profiles').select(
        'id, name, email, contact_email'
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
        contact_email = advisor.data[0].get('contact_email')
        advisor_email = advisor.data[0].get('email')
        advisor_name = advisor.data[0].get('name', 'there')
        
        # Use contact_email if set, otherwise use the main email
        user_email = contact_email or advisor_email
        
        if user_email:
            from services import email_service
            email_service.send_advisor_approved_email(user_email, advisor_name.split()[0] if advisor_name else 'there')
    except Exception as e:
        # Log but don't fail if email fails
        print(f"[ADMIN] Failed to send advisor approval email: {e}")
    
    return result.data[0]


def reject_advisor(advisor_id: str, reason: str = None):
    """Set advisor status to REJECTED and send email"""
    supabase = get_supabase()
    
    # Get advisor profile with name and contact info for email
    advisor = supabase.table('advisor_profiles').select(
        'id, name, email, contact_email'
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
        contact_email = advisor.data[0].get('contact_email')
        advisor_email = advisor.data[0].get('email')
        advisor_name = advisor.data[0].get('name', 'there')
        
        # Use contact_email if set, otherwise use the main email
        user_email = contact_email or advisor_email
        
        if user_email:
            from services import email_service
            email_service.send_advisor_rejected_email(user_email, advisor_name.split()[0] if advisor_name else 'there', reason)
    except Exception as e:
        # Log but don't fail if email fails
        print(f"[ADMIN] Failed to send advisor rejection email: {e}")
    
    return result.data[0]
