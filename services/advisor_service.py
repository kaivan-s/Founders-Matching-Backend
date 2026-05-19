"""Advisor service for managing advisor profiles, requests, and workspace access"""
from config.database import get_supabase
from .notification_service import NotificationService
from .advisor_verification_service import verify_advisor_profile
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import json

def _get_founder_id(clerk_user_id):
    """Helper to get founder ID from clerk_user_id.
    Uses request-scoped caching to avoid redundant queries.
    """
    # OPTIMIZATION: Check request cache first
    try:
        from utils.request_cache import get_cached_founder_id, set_cached_founder_id
        cached_id = get_cached_founder_id(clerk_user_id)
        if cached_id:
            return cached_id
    except ImportError:
        pass
    
    supabase = get_supabase()
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    founder_id = user_profile.data[0]['id']
    
    # Cache the result
    try:
        from utils.request_cache import set_cached_founder_id
        set_cached_founder_id(clerk_user_id, founder_id)
    except ImportError:
        pass
    
    return founder_id

def _get_or_create_founder_id(clerk_user_id, user_name=None, user_email=None):
    """Helper to get founder ID from clerk_user_id, creating a minimal profile if needed for advisors.
    
    IMPORTANT: This function is ONLY called when creating/updating advisor profiles.
    It should NEVER be called during founder profile creation. Founder profiles should
    be created through the /api/founders/onboarding or /api/founders endpoints.
    
    NOTE: Due to database schema constraints (advisor_profiles.user_id foreign key references founders.id),
    we must create a minimal founder profile for advisors. This is a technical requirement, not a business
    requirement - advisors-only users don't need to complete founder onboarding, but we need a record in
    the founders table for the foreign key relationship.
    
    If a founder exists with the same email but different clerk_user_id, updates the clerk_user_id.
    Always ensures email is set in founders table.
    """
    from utils.auth import get_clerk_user_email
    
    supabase = get_supabase()
    user_profile = supabase.table('founders').select('id, email, onboarding_completed').eq('clerk_user_id', clerk_user_id).execute()
    
    # If founder exists, ensure email is set and return the ID
    # Note: If founder already completed onboarding, we use that profile (don't overwrite)
    if user_profile.data:
        founder_id = user_profile.data[0]['id']
        existing_email = user_profile.data[0].get('email', '').strip()
        onboarding_completed = user_profile.data[0].get('onboarding_completed', False)
        
        # If email is missing, try to get it from provided params or Clerk
        if not existing_email:
            final_email = user_email
            if not final_email or not final_email.strip():
                try:
                    final_email = get_clerk_user_email(clerk_user_id)
                except:
                    pass
            
            if final_email and final_email.strip():
                supabase.table('founders').update({
                    'email': final_email.strip()
                }).eq('id', founder_id).execute()
        
        # If founder already completed onboarding, return the existing profile
        # Don't create a minimal advisor-style profile
        if onboarding_completed:
            return founder_id
        
        # If founder exists but hasn't completed onboarding, we can still use it
        # (maybe they started founder onboarding but then switched to advisor flow)
        return founder_id
    
    # Check by email if provided (case-insensitive)
    if user_email and user_email.strip():
        email_lower = user_email.strip().lower()
        all_founders = supabase.table('founders').select('id, email, clerk_user_id, onboarding_completed').execute()
        if all_founders.data:
            for founder in all_founders.data:
                founder_email = founder.get('email', '').strip().lower()
                if founder_email == email_lower:
                    # Found existing founder with same email - update clerk_user_id and return
                    # Only update if they haven't completed onboarding (to avoid overwriting)
                    if not founder.get('onboarding_completed', False):
                        supabase.table('founders').update({'clerk_user_id': clerk_user_id}).eq('id', founder['id']).execute()
                    return founder['id']
    
    # Get email from Clerk if not provided
    final_email = user_email
    if not final_email or not final_email.strip():
        try:
            final_email = get_clerk_user_email(clerk_user_id)
        except:
            pass
    
    if not final_email or not final_email.strip():
        raise ValueError("Email address is required. Please ensure your account has a valid email address.")
    
    # Get name from Clerk if not provided
    final_name = user_name
    if not final_name or not final_name.strip():
        try:
            from utils.auth import get_clerk_user_name
            final_name = get_clerk_user_name(clerk_user_id)
        except:
            pass
    
    # Create minimal founder profile for advisors ONLY (required by database schema foreign key constraint)
    # TECHNICAL NOTE: advisor_profiles.user_id has a foreign key constraint to founders.id
    # This is a database design limitation - ideally advisors wouldn't need founder profiles,
    # but the current schema requires it. The founder profile created here is minimal and
    # marked with onboarding_completed=False to indicate it's not a real founder profile.
    # 
    # This should only happen when creating an advisor profile, not during founder onboarding.
    # Advisors don't go through founder onboarding, so we create a minimal record.
    founder_data = {
        'clerk_user_id': clerk_user_id,
        'name': final_name or 'Advisor',
        'email': final_email.strip(),
        'purpose': 'both',  # Use valid purpose value (constraint requires: idea_needs_cofounder, skills_want_project, or both)
        'location': '',
        'looking_for': 'Advisor providing guidance and accountability support',
        'skills': [],
        'onboarding_completed': False,  # Advisors don't complete founder onboarding - this marks it as advisor-only
        'credits': 0  # Advisors don't need credits
    }
    
    try:
        result = supabase.table('founders').insert(founder_data).execute()
        if not result.data:
            error_msg = "Failed to create founder profile for advisor - no data returned"
            raise ValueError(error_msg)
        return result.data[0]['id']
    except Exception as e:
        error_msg = f"Failed to create founder profile for advisor: {str(e)}"
        import traceback
        traceback.print_exc()
        raise ValueError(error_msg)


def _calculate_profile_completion_score(data: dict) -> int:
    """Calculate profile completion score (0-100) based on filled fields."""
    score = 0
    max_score = 100
    
    # Basic info (20 points)
    if data.get('headline') and len(data.get('headline', '')) >= 10:
        score += 5
    if data.get('bio') and len(data.get('bio') or '') >= 100:
        score += 10
    linkedin_url = (data.get('linkedin_url') or '').strip()
    if linkedin_url and 'linkedin.com' in linkedin_url:
        score += 5
    
    # Professional background (30 points)
    bg = data.get('professional_background', {})
    if isinstance(bg, dict):
        if bg.get('years_experience'):
            score += 5
        current_role = bg.get('current_role', {})
        if isinstance(current_role, dict) and current_role.get('title') and current_role.get('company'):
            score += 10
        previous_roles = bg.get('previous_roles', [])
        if isinstance(previous_roles, list) and len(previous_roles) > 0:
            score += 5
        if bg.get('startups_advised_count'):
            score += 5
        if bg.get('notable_achievements', '').strip():
            score += 5
    
    # Expertise (15 points)
    if data.get('advisory_types') and len(data.get('advisory_types', [])) > 0:
        score += 5
    if data.get('preferred_stages') and len(data.get('preferred_stages', [])) > 0:
        score += 5
    if data.get('domains') and len(data.get('domains', [])) > 0:
        score += 5
    
    # Portfolio (20 points)
    portfolio = data.get('portfolio', {})
    if isinstance(portfolio, dict):
        if portfolio.get('personal_website', '').strip():
            score += 5
        if portfolio.get('crunchbase_url', '').strip() or portfolio.get('angellist_url', '').strip():
            score += 5
        if portfolio.get('medium_url', '').strip() or portfolio.get('youtube_url', '').strip():
            score += 5
        other_links = portfolio.get('other_links', [])
        if isinstance(other_links, list) and len(other_links) > 0:
            score += 5
    
    # Consultation setup (15 points)
    if data.get('availability_hours_per_week'):
        score += 5
    rate_30 = data.get('consultation_rate_30min_usd')
    rate_60 = data.get('consultation_rate_60min_usd')
    if rate_30 or rate_60:
        score += 5
    payment_methods = data.get('payment_methods', {})
    if isinstance(payment_methods, dict) and any(v and str(v).strip() for v in payment_methods.values()):
        score += 5
    
    return min(score, max_score)


def _calculate_verification_badges(data: dict) -> list:
    """Calculate which verification badges the advisor has earned."""
    badges = []
    
    # LinkedIn badge
    linkedin_url = data.get('linkedin_url', '').strip()
    if linkedin_url and 'linkedin.com' in linkedin_url:
        badges.append('linkedin')
    
    # Veteran badge (10+ years experience)
    bg = data.get('professional_background', {})
    if isinstance(bg, dict):
        years = bg.get('years_experience', '')
        if years in ['10-15', '15-20', '20+']:
            badges.append('veteran')
        
        # Experienced badge (2+ previous roles)
        previous_roles = bg.get('previous_roles', [])
        if isinstance(previous_roles, list) and len(previous_roles) >= 2:
            badges.append('experienced')
    
    # Portfolio badge
    portfolio = data.get('portfolio', {})
    if isinstance(portfolio, dict):
        if portfolio.get('personal_website', '').strip() or portfolio.get('crunchbase_url', '').strip():
            badges.append('portfolio')
    
    # Profile complete badge (80%+ completion)
    score = _calculate_profile_completion_score(data)
    if score >= 80:
        badges.append('profile_complete')
    
    return badges


def create_advisor_profile(clerk_user_id, data, user_name=None, user_email=None):
    """Create or update advisor profile"""
    
    # Create minimal founder profile if it doesn't exist (required by schema)
    try:
        founder_id = _get_or_create_founder_id(clerk_user_id, user_name, user_email)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise
    
    supabase = get_supabase()
    
    # Check if table exists
    try:
        test_query = supabase.table('advisor_profiles').select('id').limit(1).execute()
    except Exception as e:
        error_msg = f"Advisor profiles feature is not yet available. Please run database migrations first. Error: {str(e)}"
        raise ValueError(error_msg)
    
    # Validate required fields
    if 'headline' not in data or data.get('headline') == '':
        raise ValueError("headline is required")
    
    # Check if profile exists
    try:
        existing = supabase.table('advisor_profiles').select('id, status, max_active_workspaces').eq('user_id', founder_id).execute()
    except Exception as e:
        raise ValueError("Advisor profiles table not found. Please run database migrations.")
    
    # Handle max_active_workspaces - required for new profiles, optional for updates
    if existing.data:
        # Updating existing profile - use existing value if not provided
        max_workspaces = data.get('max_active_workspaces', existing.data[0].get('max_active_workspaces', 3))
    else:
        # Creating new profile - required
        if 'max_active_workspaces' not in data:
            raise ValueError("max_active_workspaces is required")
        max_workspaces = data['max_active_workspaces']
    
    # Validate max_active_workspaces
    try:
        max_workspaces = int(max_workspaces)
        if max_workspaces < 1 or max_workspaces > 10:
            raise ValueError("max_active_workspaces must be between 1 and 10")
    except (ValueError, TypeError) as e:
        if isinstance(e, ValueError) and "must be between" in str(e):
            raise
        raise ValueError(f"max_active_workspaces must be a number between 1 and 10. Received: {max_workspaces}")
    
    # Validate LinkedIn URL (optional - can be set via OAuth)
    linkedin_url = (data.get('linkedin_url') or '').strip()
    if linkedin_url:
        if not linkedin_url.startswith('https://'):
            raise ValueError("LinkedIn URL must start with https://")
        if 'linkedin.com' not in linkedin_url and 'linked.in' not in linkedin_url:
            raise ValueError("LinkedIn URL must be a valid LinkedIn profile URL")
    
    # Validate Twitter/X URL if provided
    twitter_url = data.get('twitter_url', '').strip()
    if twitter_url and not (twitter_url.startswith('http://') or twitter_url.startswith('https://')):
        raise ValueError("Twitter/X URL must be a valid URL starting with http:// or https://")
    
    # Pay-per-consultation pricing (USD). Either may be omitted to disable that
    # call length on the advisor's profile. Range checks come from ADVISOR_PRICING.
    from services.plan_service import ADVISOR_PRICING

    def _parse_rate(value, label):
        if value in (None, '', 0, '0'):
            return None
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a number")
        if v < ADVISOR_PRICING['minConsultationRateUSD'] or v > ADVISOR_PRICING['maxConsultationRateUSD']:
            raise ValueError(
                f"{label} must be between ${ADVISOR_PRICING['minConsultationRateUSD']} "
                f"and ${ADVISOR_PRICING['maxConsultationRateUSD']}"
            )
        return v

    rate_30 = _parse_rate(data.get('consultation_rate_30min_usd'), '30-min consultation rate')
    rate_60 = _parse_rate(data.get('consultation_rate_60min_usd'), '60-min consultation rate')

    # Direct-payment methods (advisor-level, applies across all consultations)
    payment_methods = data.get('payment_methods')
    if payment_methods is not None and not isinstance(payment_methods, dict):
        raise ValueError("payment_methods must be an object")

    # Professional background (structured)
    professional_background = data.get('professional_background')
    if professional_background is not None and not isinstance(professional_background, dict):
        raise ValueError("professional_background must be an object")
    
    # Portfolio links
    portfolio = data.get('portfolio')
    if portfolio is not None and not isinstance(portfolio, dict):
        raise ValueError("portfolio must be an object")
    
    # Calculate profile completion score
    completion_score = _calculate_profile_completion_score(data)
    
    # Calculate badges earned
    badges_earned = _calculate_verification_badges(data)
    
    profile_data = {
        'user_id': founder_id,
        'headline': data['headline'],
        'bio': data.get('bio', ''),
        'timezone': data.get('timezone', 'UTC'),
        'languages': data.get('languages', []),
        'expertise_stages': data.get('expertise_stages', []),
        'preferred_stages': data.get('preferred_stages', []),
        'advisory_types': data.get('advisory_types', []),
        'domains': data.get('domains', []),
        'max_active_workspaces': max_workspaces,
        'preferred_cadence': data.get('preferred_cadence', 'weekly'),
        'availability_hours_per_week': data.get('availability_hours_per_week'),
        'contact_email': data.get('contact_email'),
        'contact_note': data.get('contact_note'),
        'linkedin_url': linkedin_url,
        'twitter_url': twitter_url if twitter_url else None,
        # Pay-per-consultation fields
        'consultation_rate_30min_usd': rate_30,
        'consultation_rate_60min_usd': rate_60,
        'payment_methods': payment_methods if payment_methods is not None else {},
        # New verification fields
        'professional_background': professional_background if professional_background is not None else {},
        'portfolio': portfolio if portfolio is not None else {},
        'profile_completion_score': completion_score,
        'verification_badges': badges_earned,
    }

    # Cal.com scheduling URL (paste). Optional; does not require OAuth.
    if 'calcom_booking_url' in data:
        from services.calcom_service import normalize_cal_booking_url
        try:
            profile_data['calcom_booking_url'] = normalize_cal_booking_url(data.get('calcom_booking_url'))
        except ValueError as e:
            raise ValueError(str(e))

    # Handle questionnaire_data separately (JSONB field)
    questionnaire_data = data.get('questionnaire_data', {})
    if questionnaire_data and isinstance(questionnaire_data, dict) and len(questionnaire_data) > 0:
        questionnaire_completed = True
        profile_data['questionnaire_data'] = questionnaire_data
        profile_data['questionnaire_completed'] = True
        profile_data['questionnaire_completed_at'] = datetime.now(timezone.utc).isoformat()
    else:
        profile_data['questionnaire_data'] = None
        profile_data['questionnaire_completed'] = False
    
    try:
        if existing.data:
            # Update existing profile
            current_status = existing.data[0].get('status', 'PENDING')
            
            # If status is PENDING or REJECTED, keep it as PENDING (user is updating application)
            # If status is APPROVED, preserve it (admin approval should not be changed by user)
            if current_status in ('PENDING', 'REJECTED'):
                profile_data['status'] = 'PENDING'
                profile_data['is_discoverable'] = False  # Force false for pending/rejected
            else:
                # For APPROVED profiles, don't change status or is_discoverable
                # Only update other fields
                pass
            
            profile = supabase.table('advisor_profiles').update(profile_data).eq('id', existing.data[0]['id']).execute()
        else:
            # Create new profile - force PENDING status and is_discoverable = false
            profile_data['status'] = 'PENDING'
            profile_data['is_discoverable'] = False
            profile = supabase.table('advisor_profiles').insert(profile_data).execute()
        
        if not profile.data:
            error_msg = "Failed to create/update partner profile - no data returned"
            raise ValueError(error_msg)
        
        created_profile = profile.data[0]
        
        # Run automatic verification for new profiles or when status is PENDING/REJECTED
        current_status = created_profile.get('status', 'PENDING')
        if current_status in ('PENDING', 'REJECTED'):
            try:
                # Prepare profile data for verification
                verification_data = {
                    'bio': profile_data.get('bio', ''),
                    'headline': profile_data.get('headline', ''),
                    'contact_email': profile_data.get('contact_email'),
                    'user_email': user_email,
                    'questionnaire_data': profile_data.get('questionnaire_data', {})
                }
                
                # Run verification (async in production, sync for now)
                verify_advisor_profile(created_profile['id'], verification_data)
                
                # Refresh profile to get updated verification status
                updated = supabase.table('advisor_profiles').select('*').eq('id', created_profile['id']).execute()
                if updated.data:
                    created_profile = updated.data[0]
            except Exception as e:
                # Log error but don't fail profile creation
                import traceback
                traceback.print_exc()
                # Verification failure shouldn't block profile creation
        
        return created_profile
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise ValueError(f"Failed to create/update partner profile: {str(e)}")

def update_advisor_contact_info(clerk_user_id, contact_info):
    """Update contact info for partner profile"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Check if profile exists
    existing = supabase.table('advisor_profiles').select('id').eq('user_id', founder_id).execute()
    if not existing.data:
        raise ValueError("Advisor profile not found")
    
    update_data = {}
    if 'contact_email' in contact_info:
        update_data['contact_email'] = contact_info['contact_email']
    if 'meeting_link' in contact_info:
        update_data['meeting_link'] = contact_info['meeting_link']
    if 'contact_note' in contact_info:
        update_data['contact_note'] = contact_info['contact_note']
    
    if update_data:
        result = supabase.table('advisor_profiles').update(update_data).eq('id', existing.data[0]['id']).execute()
        return result.data[0] if result.data else None
    
    return None


def update_advisor_cal_booking_link(clerk_user_id: str, booking_url: Optional[str]) -> Dict[str, Any]:
    """Save or clear the advisor's public Cal.com scheduling link (paste-only, no OAuth required)."""
    from services.calcom_service import normalize_cal_booking_url

    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()

    existing = supabase.table('advisor_profiles').select('id').eq('user_id', founder_id).execute()
    if not existing.data:
        raise ValueError("Advisor profile not found")

    if booking_url is None or (isinstance(booking_url, str) and not booking_url.strip()):
        normalized = None
    else:
        try:
            normalized = normalize_cal_booking_url(booking_url)
        except ValueError as e:
            raise ValueError(str(e))

    supabase.table('advisor_profiles').update({
        'calcom_booking_url': normalized,
    }).eq('id', existing.data[0]['id']).execute()

    out = get_advisor_profile(clerk_user_id)
    if not out:
        raise ValueError("Advisor profile not found")
    return out

def get_advisor_profile(clerk_user_id):
    """Get advisor profile for current user
    
    This function tries multiple approaches to find the advisor profile:
    1. First tries to get founder_id and query advisor_profiles (original method)
    2. If that fails, tries to query all advisor_profiles with user relationship and filter in Python
    3. Includes better error logging to help debug issues
    
    Returns None if the underlying founder account is deleted (user needs to re-onboard).
    """
    from utils.logger import log_info, log_error
    
    supabase = get_supabase()
    
    # First, check if the founder account is deleted - if so, return None
    # This forces deleted users to go through onboarding again
    founder_check = supabase.table('founders').select('id, is_deleted').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if founder_check.data and founder_check.data[0].get('is_deleted'):
        log_info(f"Advisor profile blocked - founder account is deleted: {clerk_user_id}")
        return None
    
    # Method 1: Try to get founder_id first, then query advisor_profiles (original method)
    founder_id = None
    try:
        founder_id = _get_founder_id(clerk_user_id)
        log_info(f"Found founder_id: {founder_id} for clerk_user_id: {clerk_user_id}")
    except ValueError as e:
        log_info(f"Founder profile not found for clerk_user_id: {clerk_user_id}, trying alternative method")
        # Try alternative method: query advisor_profiles with user relationship and filter
        try:
            # Query all advisor_profiles with user relationship
            all_profiles = supabase.table('advisor_profiles').select(
                '*, user:founders!user_id(id, clerk_user_id, name, email)'
            ).execute()
            
            if all_profiles.data:
                # Filter by clerk_user_id in Python
                for profile in all_profiles.data:
                    user_data = profile.get('user')
                    if user_data and user_data.get('clerk_user_id') == clerk_user_id:
                        founder_id = user_data.get('id')
                        profile_data = profile.copy()
                        # Remove the user key and add it properly
                        if 'user' in profile_data:
                            profile_data['user'] = user_data
                        
                        # Calculate current_active_workspaces
                        if founder_id:
                            try:
                                active_workspaces = supabase.table('workspace_participants').select('workspace_id').eq(
                                    'user_id', founder_id
                                ).eq('role', 'ADVISOR').execute()
                            except Exception:
                                active_workspaces = supabase.table('workspace_participants').select('workspace_id').eq(
                                    'user_id', founder_id
                                ).execute()
                            
                            profile_data['current_active_workspaces'] = len(active_workspaces.data) if active_workspaces.data else 0
                        else:
                            profile_data['current_active_workspaces'] = 0
                        
                        log_info(f"Successfully retrieved advisor profile using alternative method for clerk_user_id: {clerk_user_id}")
                        return profile_data
        except Exception as e:
            log_error(f"Alternative method failed: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # If both methods fail, return None
        log_info(f"No advisor profile found for clerk_user_id: {clerk_user_id}")
        return None
    
    # Method 2: Query advisor_profiles using founder_id
    try:
        profile = supabase.table('advisor_profiles').select('*').eq('user_id', founder_id).execute()
    except Exception as e:
        log_error(f"Error querying advisor_profiles table for founder_id {founder_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
    
    if not profile.data or len(profile.data) == 0:
        log_info(f"No advisor profile found in advisor_profiles table for founder_id: {founder_id} (clerk_user_id: {clerk_user_id})")
        return None
    
    profile_data = profile.data[0]
    
    # Also fetch user info if not already included
    try:
        user_info = supabase.table('founders').select('id, clerk_user_id, name, email').eq('id', founder_id).execute()
        if user_info.data and len(user_info.data) > 0:
            profile_data['user'] = user_info.data[0]
    except Exception as e:
        log_error(f"Error fetching user info: {str(e)}")
    
    # Calculate current_active_workspaces
    try:
        active_workspaces = supabase.table('workspace_participants').select('workspace_id').eq(
            'user_id', founder_id
        ).eq('role', 'ADVISOR').execute()
    except Exception:
        # Fallback if role column doesn't exist yet - count all workspaces for this user
        active_workspaces = supabase.table('workspace_participants').select('workspace_id').eq(
            'user_id', founder_id
        ).execute()
    
    profile_data['current_active_workspaces'] = len(active_workspaces.data) if active_workspaces.data else 0
    
    log_info(f"Successfully retrieved advisor profile for clerk_user_id: {clerk_user_id}, founder_id: {founder_id}")
    return profile_data

def get_available_advisors(workspace_id, filters=None, clerk_user_id=None):
    """Get available partners for marketplace, filtered by workspace attributes.

    When strict filters (notably expertise_stages vs workspace stage) yield only a
    small set, we automatically broaden to all approved discoverable advisors
    (same domain filter if applied) so sparse markets still show options.
    Results are sorted with best stage match first.
    """
    from datetime import datetime, timezone
    from utils.logger import log_info

    MIN_BEFORE_BROADEN = 5
    DEFAULT_MAX_ACTIVE = 3

    supabase = get_supabase()
    filters = filters or {}

    # Get workspace info for filtering (only select stage, domain column may not exist)
    try:
        workspace = supabase.table('workspaces').select('stage, domain').eq('id', workspace_id).execute()
    except Exception:
        workspace = supabase.table('workspaces').select('stage').eq('id', workspace_id).execute()

    workspace_data = workspace.data[0] if workspace.data else {}
    workspace_stage = workspace_data.get('stage') or 'idea'
    workspace_domain = workspace_data.get('domain', '')

    stage_mapping = {
        'idea': 'idea',
        'mvp': 'pre-seed',
        'revenue': 'seed',
        'other': 'idea',
    }
    mapped_stage = stage_mapping.get(workspace_stage, 'idea')

    def _norm_expertise_stages(profile):
        es = profile.get('expertise_stages') or []
        if not isinstance(es, list):
            return []
        return [str(x).strip().lower() for x in es]

    def _profile_matches_mapped_stage(profile):
        return mapped_stage.lower() in _norm_expertise_stages(profile)

    def _base_query():
        q = supabase.table('advisor_profiles').select(
            '*, user:founders!user_id(id, name, email, clerk_user_id)'
        ).eq('status', 'APPROVED').eq('is_discoverable', True)
        if workspace_domain and filters.get('domain'):
            q = q.contains('domains', [workspace_domain])
        return q

    def _execute_advisor_query(q):
        try:
            return q.execute()
        except Exception as e:
            error_msg = str(e)
            if 'PGRST205' in error_msg or 'Could not find the table' in error_msg:
                raise ValueError(
                    "The advisor_profiles table does not exist. "
                    "Please run the database migration: backend/migrations/001_create_accountability_partner_tables.sql"
                ) from e
            raise

    strict_query = _base_query().contains('expertise_stages', [mapped_stage])
    profiles_result = _execute_advisor_query(strict_query)
    profile_rows = profiles_result.data or []

    # Get founder_id to check for existing requests
    try:
        founder_id = _get_founder_id(clerk_user_id)
    except ValueError:
        founder_id = None

    existing_requests = {}
    if founder_id:
        try:
            requests = supabase.table('advisor_requests').select('advisor_user_id, status').eq(
                'workspace_id', workspace_id
            ).eq('founder_user_id', founder_id).execute()
            if requests.data:
                for req in requests.data:
                    existing_requests[req['advisor_user_id']] = req['status']
        except Exception:
            pass

    def _can_accept_bookings(p):
        """Mirror of consultation_service._advisor_can_accept_bookings, inlined
        to avoid an import cycle."""
        status = p.get('subscription_status') or 'free'
        now = datetime.now(timezone.utc)
        if status == 'free':
            return True
        if status == 'trial':
            ends = p.get('trial_ends_at')
            if not ends:
                return True
            try:
                t = datetime.fromisoformat(str(ends).replace('Z', '+00:00'))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                return t > now
            except (ValueError, AttributeError):
                return False
        if status == 'active':
            ends = p.get('subscription_current_period_end')
            if not ends:
                return True
            try:
                t = datetime.fromisoformat(str(ends).replace('Z', '+00:00'))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                return t > now
            except (ValueError, AttributeError):
                return False
        return False

    def _effective_max_active(profile):
        raw = profile.get('max_active_workspaces')
        if raw is None or raw < 1:
            return DEFAULT_MAX_ACTIVE
        try:
            return min(10, int(raw))
        except (TypeError, ValueError):
            return DEFAULT_MAX_ACTIVE

    def _build_available_partners(rows, marketplace_broadened: bool):
        out = []
        for profile in rows:
            user_id = profile['user_id']

            if not _can_accept_bookings(profile):
                continue

            workspace_participant = supabase.table('workspace_participants').select('id, role').eq(
                'workspace_id', workspace_id
            ).eq('user_id', user_id).execute()

            if workspace_participant.data:
                participant_role = workspace_participant.data[0].get('role')
                if participant_role != 'ADVISOR':
                    continue

            try:
                active_count = supabase.table('workspace_participants').select('workspace_id').eq(
                    'user_id', user_id
                ).eq('role', 'ADVISOR').execute()
            except Exception:
                active_count = supabase.table('workspace_participants').select('workspace_id').eq(
                    'user_id', user_id
                ).execute()

            current_active = len(active_count.data) if active_count.data else 0
            max_active = _effective_max_active(profile)

            if current_active >= max_active:
                continue

            stage_match = _profile_matches_mapped_stage(profile)
            
            # Get name, fixing it from Clerk if it's missing or placeholder
            user_data = profile.get('user') or {}
            user_name = user_data.get('name') or ''
            clerk_user_id_for_name = user_data.get('clerk_user_id')
            
            # If name is missing, use email prefix as display name
            if not user_name or user_name.strip().lower() in ['', 'advisor', 'unknown']:
                contact_email = profile.get('contact_email') or user_data.get('email') or ''
                if contact_email and '@' in contact_email:
                    user_name = contact_email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
                    # Save it to database for future
                    try:
                        founder_id = user_data.get('id')
                        if founder_id:
                            supabase.table('founders').update({'name': user_name}).eq('id', founder_id).execute()
                    except:
                        pass
            
            # Update profile's user object with corrected name
            if user_name and user_data:
                profile['user'] = {**user_data, 'name': user_name}

            out.append({
                **profile,
                'current_active_workspaces': current_active,
                'available_slots': max_active - current_active,
                'request_status': existing_requests.get(user_id),
                'marketplace_stage_match': stage_match,
                'marketplace_broadened': marketplace_broadened,
                '_sort_name': user_name,
            })

        out.sort(
            key=lambda p: (
                0 if p.get('marketplace_stage_match') else 1,
                p.get('_sort_name', ''),
            )
        )
        for p in out:
            p.pop('_sort_name', None)
        return out

    available_partners = _build_available_partners(profile_rows, marketplace_broadened=False)

    if len(available_partners) >= MIN_BEFORE_BROADEN:
        pass
    else:
        broad_query = _base_query()
        broad_result = _execute_advisor_query(broad_query)
        broad_rows = broad_result.data or []
        broad_partners = _build_available_partners(broad_rows, marketplace_broadened=True)
        strict_n = len(available_partners)
        if len(broad_partners) > strict_n:
            log_info(
                f"Workspace advisor marketplace broadened: workspace_id={workspace_id} "
                f"strict_count={strict_n} broad_count={len(broad_partners)} mapped_stage={mapped_stage}"
            )
        available_partners = broad_partners

    if available_partners:
        advisor_ids = [p['user_id'] for p in available_partners]
        rating_stats = _batch_get_advisor_ratings(supabase, advisor_ids)
        for partner in available_partners:
            uid = partner['user_id']
            partner['rating_stats'] = rating_stats.get(uid, {
                'avg_rating': None,
                'total_reviews': 0,
            })

    return available_partners


def _batch_get_advisor_ratings(supabase, advisor_ids):
    """Batch-fetch rating stats for multiple advisors.

    Returns a dict mapping user_id -> {avg_rating, total_reviews}.
    """
    if not advisor_ids:
        return {}

    try:
        # Fetch all public founder reviews for these advisors
        res = supabase.table('advisor_consultation_reviews').select(
            'reviewee_id, rating'
        ).in_('reviewee_id', advisor_ids).eq(
            'reviewer_role', 'founder'
        ).eq('is_public', True).execute()

        reviews = res.data or []
    except Exception:
        # Table may not exist yet; return empty stats
        return {}

    # Group by reviewee_id and calculate stats
    from collections import defaultdict
    grouped = defaultdict(list)
    for r in reviews:
        grouped[r['reviewee_id']].append(r['rating'])

    stats = {}
    for advisor_id in advisor_ids:
        ratings = grouped.get(advisor_id, [])
        if ratings:
            stats[advisor_id] = {
                'avg_rating': round(sum(ratings) / len(ratings), 2),
                'total_reviews': len(ratings),
            }
        else:
            stats[advisor_id] = {
                'avg_rating': None,
                'total_reviews': 0,
            }

    return stats


# Legacy workspace-joining advisor functions removed.
# Advisors now provide booking-only 1-1 consultations via Cal.com.
# Removed functions: _verify_workspace_access, create_advisor_request,
# get_advisor_requests, get_active_workspaces, respond_to_advisor_request,
# remove_advisor_from_workspace, compute_advisor_impact_scorecard, save_quarterly_review

