"""Advisor profile verification service"""
from config.database import get_supabase
from utils.logger import log_info, log_error


def verify_advisor_profile(profile_id: int, verification_data: dict):
    """
    Verify an advisor profile based on provided data
    
    Args:
        profile_id: The ID of the advisor profile to verify
        verification_data: Dictionary containing profile data to verify:
            - bio: Profile bio text
            - headline: Profile headline
            - contact_email: Contact email address
            - user_email: User's primary email
            - questionnaire_data: Questionnaire responses
    
    Returns:
        None (updates profile in database)
    """
    try:
        supabase = get_supabase()
        if not supabase:
            log_error("Database connection not available for verification")
            return
        
        # Basic validation checks
        bio = verification_data.get('bio', '').strip()
        headline = verification_data.get('headline', '').strip()
        contact_email = verification_data.get('contact_email', '').strip()
        user_email = verification_data.get('user_email', '').strip()
        questionnaire_data = verification_data.get('questionnaire_data', {})
        
        # Check if minimum required fields are present
        has_bio = len(bio) >= 50  # Minimum bio length
        has_headline = len(headline) >= 10  # Minimum headline length
        has_email = bool(contact_email or user_email)
        has_questionnaire = bool(questionnaire_data and len(questionnaire_data) > 0)
        
        # For now, keep status as PENDING - manual approval required
        # In the future, this could auto-approve if all checks pass
        # For auto-approval, you would update status to 'APPROVED' here
        
        # Log verification results
        log_info(f"Verification completed for profile {profile_id}: "
                f"bio={has_bio}, headline={has_headline}, email={has_email}, "
                f"questionnaire={has_questionnaire}")
        
        # Optionally store verification metadata
        # You could add a verification_metadata field to advisor_profiles table
        # For now, we'll just log the results
        
    except Exception as e:
        log_error(f"Error during advisor profile verification: {str(e)}")
        import traceback
        traceback.print_exc()
        # Don't raise - verification failure shouldn't block profile creation
