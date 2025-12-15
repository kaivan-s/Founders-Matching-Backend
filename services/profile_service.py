"""Profile-related business logic"""
import traceback
from config.database import get_supabase

def check_profile(clerk_user_id):
    """Check if user has a profile"""
    supabase = get_supabase()
    
    profile = supabase.table('founders').select('*').eq('clerk_user_id', clerk_user_id).execute()
    if profile.data:
        return {"has_profile": True, "profile": profile.data[0]}
    else:
        return {"has_profile": False}

def get_credits(clerk_user_id):
    """Get current user's credits"""
    supabase = get_supabase()
    
    profile = supabase.table('founders').select('credits').eq('clerk_user_id', clerk_user_id).execute()
    
    
    if profile.data:
        credits_value = profile.data[0].get('credits')
        # Default to 0 if credits is None or not set
        credits_value = credits_value if credits_value is not None else 0
        return {"credits": credits_value}
    else:
        raise ValueError("Profile not found")

def debug_profile(clerk_user_id):
    """Debug endpoint to check user profile and credits"""
    supabase = get_supabase()
    
    profile = supabase.table('founders').select('*').eq('clerk_user_id', clerk_user_id).execute()
    
    if profile.data:
        return {
            "found": True,
            "profile": profile.data[0],
            "credits": profile.data[0].get('credits'),
            "credits_type": type(profile.data[0].get('credits')).__name__
        }
    else:
        raise ValueError("Profile not found")

