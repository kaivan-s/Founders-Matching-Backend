"""Payment service for payment history

NOTE: Subscription checkout and webhook handling is in subscription_service.py
"""
from config.database import get_supabase


def get_payment_history(clerk_user_id):
    """
    Get payment history for a user
    
    Args:
        clerk_user_id: The Clerk user ID
    
    Returns:
        list: Payment records ordered by most recent first
    """
    supabase = get_supabase()
    
    payments = supabase.table('payments').select('*').eq('clerk_user_id', clerk_user_id).order('created_at', desc=True).execute()
    
    if not payments.data:
        return []
    
    return payments.data
