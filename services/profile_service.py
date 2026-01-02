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

