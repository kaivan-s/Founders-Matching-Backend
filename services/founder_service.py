"""Founder-related business logic"""
from config.database import get_supabase


def _get_or_create_founder_by_email(clerk_user_id, email, founder_data=None):
    """
    Get existing founder by email or clerk_user_id, or create a new one.
    If a founder exists with the same email but different clerk_user_id, update the clerk_user_id.
    
    Args:
        clerk_user_id: The Clerk user ID
        email: The email address (case-insensitive check)
        founder_data: Optional dict with founder data to use for creation/update
    
    Returns:
        tuple: (founder_id, is_new) - founder ID and whether a new founder was created
    """
    supabase = get_supabase()
    if not supabase:
        raise Exception("Database connection not available")
    
    # First, check by clerk_user_id
    existing_by_clerk = supabase.table('founders').select('id, email').eq('clerk_user_id', clerk_user_id).execute()
    if existing_by_clerk.data:
        return existing_by_clerk.data[0]['id'], False
    
    # If email is provided, check for existing founder by email (case-insensitive)
    if email and email.strip():
        email_lower = email.strip().lower()
        # Use ilike for case-insensitive email match
        email_match = supabase.table('founders').select('id, email, clerk_user_id').ilike(
            'email', email_lower
        ).limit(1).execute()
        
        if email_match.data:
            founder = email_match.data[0]
            # Found existing founder with same email - update clerk_user_id
            supabase.table('founders').update({'clerk_user_id': clerk_user_id}).eq('id', founder['id']).execute()
            # If founder_data is provided, update other fields too
            if founder_data:
                update_data = {k: v for k, v in founder_data.items() if k != 'clerk_user_id' and k != 'email' and v is not None}
                if update_data:
                    supabase.table('founders').update(update_data).eq('id', founder['id']).execute()
            return founder['id'], False
    
    # No existing founder found - create new one
    if not founder_data:
        raise ValueError("founder_data is required when creating a new founder")
    
    founder_data['clerk_user_id'] = clerk_user_id
    if email:
        founder_data['email'] = email
    
    result = supabase.table('founders').insert(founder_data).execute()
    if not result.data:
        raise Exception("Failed to create founder profile")
    
    return result.data[0]['id'], True


def create_founder(data):
    """Create a new founder profile with projects"""
    supabase = get_supabase()
    
    if supabase is None:
        raise Exception("Database connection not available")
    
    # Extract projects from request
    projects = data.get('projects', [])
    if not isinstance(projects, list):
        projects = []
    
    # Validate required fields
    clerk_user_id = data.get('clerk_user_id')
    email = data.get('email', '').strip()
    if not clerk_user_id:
        raise ValueError("clerk_user_id is required")
    if not email:
        raise ValueError("email is required")
    if not data.get('name'):
        raise ValueError("name is required")
    
    # Create founder profile
    founder_data = {
        'name': data.get('name'),
        'profile_picture_url': data.get('profile_picture_url'),
        'looking_for': data.get('looking_for'),
        'compatibility_answers': data.get('compatibility_answers', {}),
        'skills': data.get('skills', []),
        'location': data.get('location'),
        'website_url': data.get('website_url'),
        'linkedin_url': data.get('linkedin_url')
    }
    
    # Get or create founder (checks for existing email)
    founder_id, is_new = _get_or_create_founder_by_email(clerk_user_id, email, founder_data)
    
    # If founder already existed, fetch the updated founder data
    if not is_new:
        founder_response = supabase.table('founders').select('*').eq('id', founder_id).execute()
        if not founder_response.data:
            raise Exception("Failed to retrieve founder profile")
    else:
        # New founder was created, get the inserted data
        founder_response = supabase.table('founders').select('*').eq('id', founder_id).execute()
        if not founder_response.data:
            raise Exception("Failed to retrieve founder profile")
    
    # Create projects (only if they don't already exist)
    valid_projects = [p for p in projects if p.get('title') and p.get('description') and p.get('stage')]
    
    created_projects = []
    for idx, project in enumerate(valid_projects):
        project_data = {
            'founder_id': founder_id,
            'title': project.get('title'),
            'description': project.get('description'),
            'stage': project.get('stage'),
            'display_order': idx
        }
        try:
            project_response = supabase.table('projects').insert(project_data).execute()
            if project_response.data and len(project_response.data) > 0:
                created_projects.append(project_response.data[0])
        except Exception:
            # Project might already exist, skip it
            pass
    
    # Return founder with projects
    founder_response.data[0]['projects'] = created_projects
    return founder_response.data[0]


def get_founder_by_clerk_id(clerk_user_id):
    """Get founder profile by clerk user ID"""
    supabase = get_supabase()
    
    founder = supabase.table('founders').select('*').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        return None
    
    return founder.data[0]


def get_founder_by_id(founder_id):
    """Get founder profile by founder ID"""
    supabase = get_supabase()
    
    founder = supabase.table('founders').select('*').eq('id', founder_id).execute()
    if not founder.data:
        return None
    
    return founder.data[0]


def update_founder_discovery_mode(clerk_user_id, mode):
    """Update founder's discovery mode (owner/seeker/both)"""
    if mode not in ['owner', 'seeker', 'both']:
        raise ValueError("Mode must be 'owner', 'seeker', or 'both'")
    
    supabase = get_supabase()
    
    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        raise ValueError("Founder not found")
    
    supabase.table('founders').update({
        'discovery_mode': mode
    }).eq('id', founder.data[0]['id']).execute()
    
    return {"success": True, "mode": mode}
