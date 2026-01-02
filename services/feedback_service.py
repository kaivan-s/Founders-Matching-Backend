"""Product feedback service"""
from config.database import get_supabase
from utils.logger import log_error, log_info
from utils.validation import sanitize_string, validate_enum

def create_feedback(clerk_user_id, title, description, category='Other', workspace_id=None):
    """Create a new feedback entry"""
    try:
        # Validate inputs
        if not title or not title.strip():
            raise ValueError("Title is required")
        if not description or not description.strip():
            raise ValueError("Description is required")
        
        title = sanitize_string(title, max_length=200)
        description = sanitize_string(description, max_length=5000)
        
        # Validate category and map to correct case for database constraint
        allowed_categories = ['Bug', 'UX', 'Feature', 'Pricing', 'Other']
        category_upper = validate_enum(category, allowed_categories, case_sensitive=False)
        if category_upper:
            # Map back to correct case from allowed list
            category = next((c for c in allowed_categories if c.upper() == category_upper), 'Other')
        else:
            category = 'Other'
        
        # Validate length
        if len(title.strip()) < 3:
            raise ValueError("Title must be at least 3 characters")
        if len(description.strip()) < 10:
            raise ValueError("Description must be at least 10 characters")
        if len(description.strip()) > 5000:
            raise ValueError("Description must be less than 5000 characters")
        
        supabase = get_supabase()
        if not supabase:
            raise Exception("Database connection not available")
        
        # Get user's founder ID from clerk_user_id
        founder_result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).single().execute()
        
        if not founder_result.data:
            raise ValueError("User profile not found")
        
        user_id = founder_result.data['id']
        
        # Prepare feedback data
        feedback_data = {
            'user_id': user_id,
            'title': title.strip(),
            'description': description.strip(),
            'category': category,
            'status': 'New',
            'workspace_id': workspace_id if workspace_id else None
        }
        
        # Insert feedback
        result = supabase.table('product_feedback').insert(feedback_data).execute()
        
        if not result.data:
            raise Exception("Failed to create feedback")
        
        log_info(f"Feedback created: {result.data[0]['id']} by user {clerk_user_id}")
        return result.data[0]
        
    except Exception as e:
        log_error(f"Error creating feedback: {str(e)}")
        raise

def get_user_feedback(clerk_user_id):
    """Get all feedback entries for a user"""
    try:
        supabase = get_supabase()
        if not supabase:
            raise Exception("Database connection not available")
        
        # Get user's founder ID
        founder_result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).single().execute()
        
        if not founder_result.data:
            raise ValueError("User profile not found")
        
        user_id = founder_result.data['id']
        
        # Fetch feedback ordered by created_at desc, excluding admin fields
        result = supabase.table('product_feedback')\
            .select('id, workspace_id, title, description, category, status, reward_amount_cents, reward_paid, reward_paid_at, created_at, updated_at')\
            .eq('user_id', user_id)\
            .order('created_at', desc=True)\
            .execute()
        
        return result.data if result.data else []
        
    except Exception as e:
        log_error(f"Error fetching user feedback: {str(e)}")
        raise

def update_feedback_admin(feedback_id, status=None, usefulness_score=None, reward_amount_cents=None, reward_paid=None):
    """Admin-only: Update feedback status and reward fields"""
    try:
        supabase = get_supabase()
        if not supabase:
            raise Exception("Database connection not available")
        
        update_data = {}
        
        if status is not None:
            status = validate_enum(status, ['New', 'Under review', 'Planned', 'In progress', 'Implemented', 'Rejected'], case_sensitive=False)
            if not status:
                raise ValueError("Invalid status")
            update_data['status'] = status
        
        if usefulness_score is not None:
            if not isinstance(usefulness_score, int) or usefulness_score < 0 or usefulness_score > 100:
                raise ValueError("Usefulness score must be between 0 and 100")
            update_data['usefulness_score'] = usefulness_score
        
        if reward_amount_cents is not None:
            if not isinstance(reward_amount_cents, int) or reward_amount_cents < 0:
                raise ValueError("Reward amount must be a non-negative integer")
            update_data['reward_amount_cents'] = reward_amount_cents
        
        if reward_paid is not None:
            update_data['reward_paid'] = bool(reward_paid)
            if reward_paid:
                from datetime import datetime, timezone
                update_data['reward_paid_at'] = datetime.now(timezone.utc).isoformat()
        
        if not update_data:
            raise ValueError("No fields to update")
        
        update_data['updated_at'] = 'now()'
        
        # Update feedback (using service role, bypasses RLS)
        result = supabase.table('product_feedback')\
            .update(update_data)\
            .eq('id', feedback_id)\
            .execute()
        
        if not result.data:
            raise Exception("Feedback not found or update failed")
        
        log_info(f"Feedback {feedback_id} updated: {update_data}")
        return result.data[0]
        
    except Exception as e:
        log_error(f"Error updating feedback: {str(e)}")
        raise

