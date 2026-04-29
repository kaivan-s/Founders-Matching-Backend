"""Profile-related business logic"""
import traceback
from typing import Dict, Any, Optional, List
from config.database import get_supabase
from utils.logger import log_info, log_error


# Available industry/genre interests
AVAILABLE_INTERESTS = [
    'AI/ML', 'B2B SaaS', 'B2C', 'Blockchain/Web3', 'Climate/Cleantech',
    'Consumer Apps', 'Developer Tools', 'E-commerce', 'EdTech', 'Enterprise',
    'Fintech', 'Gaming', 'Hardware', 'Healthcare/Biotech', 'IoT',
    'Logistics', 'Marketplace', 'Media/Entertainment', 'PropTech', 'Robotics',
    'Social Impact', 'Sports', 'Travel/Hospitality', 'Other'
]

# Work commitment options
COMMITMENT_OPTIONS = ['full_time', 'part_time', 'flexible', 'advisory']

# Location preference options
LOCATION_PREFERENCES = ['remote', 'hybrid', 'in_person', 'flexible']


def _get_founder_id(clerk_user_id: str) -> str:
    """Get founder ID from Clerk user ID"""
    supabase = get_supabase()
    result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not result.data:
        raise ValueError("Founder not found")
    return result.data[0]['id']


def check_profile(clerk_user_id):
    """Check if user has a profile"""
    supabase = get_supabase()
    
    profile = supabase.table('founders').select('*').eq('clerk_user_id', clerk_user_id).execute()
    if profile.data:
        return {"has_profile": True, "profile": profile.data[0]}
    else:
        return {"has_profile": False}


def debug_profile(clerk_user_id):
    """Debug endpoint to check user profile"""
    supabase = get_supabase()
    
    profile = supabase.table('founders').select('*').eq('clerk_user_id', clerk_user_id).execute()
    
    if profile.data:
        return {
            "found": True,
            "profile": profile.data[0]
        }
    else:
        raise ValueError("Profile not found")


def get_profile(clerk_user_id: str) -> Dict[str, Any]:
    """
    Get the full profile for the current user (for editing)
    
    Returns all profile fields including new enhanced fields
    """
    supabase = get_supabase()
    
    profile = supabase.table('founders').select('''
        id, clerk_user_id, name, email, location, skills,
        headline, bio, interests, expertise_details, past_projects,
        work_preferences, looking_for_description,
        linkedin_url, linkedin_verified, twitter_url, portfolio_url, github_url,
        profile_picture_url, purpose, plan, created_at, onboarding_completed
    ''').eq('clerk_user_id', clerk_user_id).execute()
    
    if not profile.data:
        raise ValueError("Profile not found")
    
    founder = profile.data[0]
    
    # Ensure defaults for new fields
    return {
        **founder,
        'headline': founder.get('headline') or '',
        'bio': founder.get('bio') or '',
        'interests': founder.get('interests') or [],
        'expertise_details': founder.get('expertise_details') or {},
        'past_projects': founder.get('past_projects') or [],
        'work_preferences': founder.get('work_preferences') or {},
        'looking_for_description': founder.get('looking_for_description') or '',
        'twitter_url': founder.get('twitter_url') or '',
        'portfolio_url': founder.get('portfolio_url') or '',
        'github_url': founder.get('github_url') or '',
    }


def get_public_profile(founder_id: str, viewer_clerk_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get a founder's public profile (for viewing by others)
    
    Excludes sensitive fields like email, clerk_user_id, etc.
    Includes verification tier + public-safe verification signals so other
    founders see badges when browsing.
    """
    from services.verification_service import compute_verification_tier, VERIFICATION_TIERS

    supabase = get_supabase()

    profile = supabase.table('founders').select('''
        id, name, location, skills, email,
        headline, bio, interests, expertise_details, past_projects,
        work_preferences, looking_for_description,
        linkedin_url, linkedin_verified, linkedin_data,
        github_verified, github_data,
        twitter_url, portfolio_url, github_url,
        profile_picture_url, purpose, created_at
    ''').eq('id', founder_id).execute()

    if not profile.data:
        raise ValueError("Profile not found")

    founder = profile.data[0]

    # Active projects count (public metric)
    projects = supabase.table('projects').select('id', count='exact').eq(
        'founder_id', founder_id
    ).eq('is_active', True).eq('is_deleted', False).execute()

    # Compute verification tier from raw signals
    tier_name = compute_verification_tier(founder)
    tier_info = VERIFICATION_TIERS[tier_name]

    # Strip sensitive bits from linkedin/github_data before surfacing publicly
    li_data = founder.get('linkedin_data') or {}
    gh_data = founder.get('github_data') or {}

    public_linkedin = {
        'verified': bool(founder.get('linkedin_verified')),
        'name': li_data.get('name'),
        'picture': li_data.get('picture'),
    } if founder.get('linkedin_verified') else None

    public_github = {
        'verified': bool(founder.get('github_verified')),
        'login': gh_data.get('login'),
        'public_repos': gh_data.get('public_repos'),
        'followers': gh_data.get('followers'),
        'account_age_years': gh_data.get('account_age_years'),
        'top_languages': gh_data.get('top_languages'),
        'total_stars': gh_data.get('total_stars'),
    } if founder.get('github_verified') else None

    # Strip private fields from the response payload
    response = {k: v for k, v in founder.items() if k not in {'email', 'linkedin_data', 'github_data'}}

    response.update({
        'headline': founder.get('headline') or '',
        'bio': founder.get('bio') or '',
        'interests': founder.get('interests') or [],
        'expertise_details': founder.get('expertise_details') or {},
        'past_projects': founder.get('past_projects') or [],
        'work_preferences': founder.get('work_preferences') or {},
        'looking_for_description': founder.get('looking_for_description') or '',
        'active_projects_count': projects.count or 0,
        'verification': {
            'tier': tier_name,
            'tier_level': tier_info['level'],
            'tier_label': tier_info['label'],
            'tier_badge': tier_info['badge'],
            'linkedin': public_linkedin,
            'github': public_github,
        },
    })
    return response


def update_profile(clerk_user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update founder profile with new data
    
    Validates and sanitizes input before updating
    """
    supabase = get_supabase()
    
    # Get current founder
    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        raise ValueError("Profile not found")
    
    founder_id = founder.data[0]['id']
    
    # Build update data, only including provided fields
    update_data = {}
    
    # Basic text fields
    if 'name' in data:
        name = data['name']
        if name and len(name.strip()) > 0:
            update_data['name'] = name.strip()[:200]
    
    if 'headline' in data:
        headline = data.get('headline') or ''
        update_data['headline'] = headline.strip()[:200]
    
    if 'bio' in data:
        bio = data.get('bio') or ''
        update_data['bio'] = bio.strip()[:2000]
    
    if 'location' in data:
        location = data.get('location') or ''
        update_data['location'] = location.strip()[:200]
    
    if 'looking_for_description' in data:
        looking_for = data.get('looking_for_description') or ''
        update_data['looking_for_description'] = looking_for.strip()[:1000]
    
    # Array fields
    if 'skills' in data:
        skills = data.get('skills') or []
        if isinstance(skills, list):
            update_data['skills'] = [s.strip()[:100] for s in skills[:30] if s and s.strip()]
    
    if 'interests' in data:
        interests = data.get('interests') or []
        if isinstance(interests, list):
            # Validate against allowed interests or allow custom
            update_data['interests'] = [i.strip()[:100] for i in interests[:20] if i and i.strip()]
    
    # JSON fields
    if 'expertise_details' in data:
        expertise = data.get('expertise_details') or {}
        if isinstance(expertise, dict):
            update_data['expertise_details'] = {
                'years_experience': expertise.get('years_experience', ''),
                'key_achievements': expertise.get('key_achievements', '')[:1000] if expertise.get('key_achievements') else '',
                'specializations': expertise.get('specializations', [])[:10] if isinstance(expertise.get('specializations'), list) else [],
            }
    
    if 'past_projects' in data:
        past_projects = data.get('past_projects') or []
        if isinstance(past_projects, list):
            validated_projects = []
            for proj in past_projects[:10]:  # Max 10 past projects
                if isinstance(proj, dict):
                    validated_projects.append({
                        'title': (proj.get('title') or '')[:200],
                        'role': (proj.get('role') or '')[:100],
                        'years': (proj.get('years') or '')[:50],
                        'outcome': (proj.get('outcome') or '')[:100],
                        'description': (proj.get('description') or '')[:500],
                    })
            update_data['past_projects'] = validated_projects
    
    if 'work_preferences' in data:
        prefs = data.get('work_preferences') or {}
        if isinstance(prefs, dict):
            update_data['work_preferences'] = {
                'commitment': prefs.get('commitment', 'flexible'),
                'location_preference': prefs.get('location_preference', 'flexible'),
                'timezone': (prefs.get('timezone') or '')[:100],
            }
    
    # URL fields
    if 'linkedin_url' in data:
        url = data.get('linkedin_url') or ''
        update_data['linkedin_url'] = url.strip()[:500] if url else None
    
    if 'twitter_url' in data:
        url = data.get('twitter_url') or ''
        update_data['twitter_url'] = url.strip()[:500] if url else None
    
    if 'portfolio_url' in data:
        url = data.get('portfolio_url') or ''
        update_data['portfolio_url'] = url.strip()[:500] if url else None
    
    if 'github_url' in data:
        url = data.get('github_url') or ''
        update_data['github_url'] = url.strip()[:500] if url else None
    
    if not update_data:
        raise ValueError("No valid fields to update")
    
    # Perform update
    result = supabase.table('founders').update(update_data).eq('id', founder_id).execute()
    
    if not result.data:
        raise ValueError("Failed to update profile")
    
    log_info(f"Updated profile for founder {founder_id}")
    
    # Return updated profile
    return get_profile(clerk_user_id)


def get_profile_completeness(clerk_user_id: str) -> Dict[str, Any]:
    """
    Calculate how complete a founder's profile is
    
    Returns a score and list of missing fields
    """
    try:
        profile = get_profile(clerk_user_id)
    except ValueError:
        return {'score': 0, 'missing': ['profile'], 'complete': False}
    
    fields_weights = {
        'name': 10,
        'headline': 15,
        'bio': 10,
        'location': 5,
        'skills': 15,
        'interests': 10,
        'expertise_details': 10,
        'past_projects': 10,
        'work_preferences': 5,
        'looking_for_description': 5,
        'linkedin_url': 5,
    }
    
    total_weight = sum(fields_weights.values())
    earned_weight = 0
    missing = []
    
    # Check each field
    if profile.get('name'):
        earned_weight += fields_weights['name']
    else:
        missing.append('name')
    
    if profile.get('headline'):
        earned_weight += fields_weights['headline']
    else:
        missing.append('headline')
    
    if profile.get('bio'):
        earned_weight += fields_weights['bio']
    else:
        missing.append('bio')
    
    if profile.get('location'):
        earned_weight += fields_weights['location']
    else:
        missing.append('location')
    
    if profile.get('skills') and len(profile['skills']) > 0:
        earned_weight += fields_weights['skills']
    else:
        missing.append('skills')
    
    if profile.get('interests') and len(profile['interests']) > 0:
        earned_weight += fields_weights['interests']
    else:
        missing.append('interests')
    
    expertise = profile.get('expertise_details') or {}
    if expertise.get('years_experience') or expertise.get('key_achievements'):
        earned_weight += fields_weights['expertise_details']
    else:
        missing.append('expertise_details')
    
    if profile.get('past_projects') and len(profile['past_projects']) > 0:
        earned_weight += fields_weights['past_projects']
    else:
        missing.append('past_projects')
    
    work_prefs = profile.get('work_preferences') or {}
    if work_prefs.get('commitment') or work_prefs.get('location_preference'):
        earned_weight += fields_weights['work_preferences']
    else:
        missing.append('work_preferences')
    
    if profile.get('looking_for_description'):
        earned_weight += fields_weights['looking_for_description']
    else:
        missing.append('looking_for_description')
    
    if profile.get('linkedin_url'):
        earned_weight += fields_weights['linkedin_url']
    else:
        missing.append('linkedin_url')
    
    score = int((earned_weight / total_weight) * 100)
    
    return {
        'score': score,
        'missing': missing,
        'complete': score >= 80,  # Consider 80%+ as "complete"
    }


def get_available_interests() -> List[str]:
    """Return list of available interest/industry options"""
    return AVAILABLE_INTERESTS


def get_work_preference_options() -> Dict[str, List[str]]:
    """Return available work preference options"""
    return {
        'commitment': COMMITMENT_OPTIONS,
        'location_preference': LOCATION_PREFERENCES,
    }

