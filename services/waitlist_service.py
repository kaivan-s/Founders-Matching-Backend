"""Waitlist-related business logic"""
from config.database import get_supabase

def join_waitlist(email):
    """Add email to waitlist"""
    email = email.strip().lower()
    
    if not email:
        raise ValueError("Email is required")
    
    # Basic email validation
    if '@' not in email or '.' not in email.split('@')[1]:
        raise ValueError("Invalid email format")
    
    supabase = get_supabase()
    
    # Check if email already exists
    existing = supabase.table('waitlist').select('email').eq('email', email).execute()
    if existing.data:
        return {
            "message": "You're already on the waitlist!",
            "email": email,
            "already_exists": True
        }
    
    # Insert email into waitlist
    result = supabase.table('waitlist').insert({'email': email}).execute()
    
    return {
        "message": "Successfully joined the waitlist!",
        "email": email,
        "already_exists": False
    }

