"""Swipe-related business logic"""
import traceback
from config.database import get_supabase
from services import plan_service

def create_swipe(clerk_user_id, data):
    """Record a swipe action - uses plan-based limits instead of credits
    Now supports project-based swiping where users swipe on specific projects
    Limits are enforced via plan_service.check_discovery_limit()
    """
    supabase = get_supabase()
    
    # Get current user's founder profile
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    swiper_id = user_profile.data[0]['id']
    swiped_id = data.get('swiped_id')
    swipe_type = data.get('swipe_type')
    project_id = data.get('project_id')  # The project being swiped on (one project, two founders)
    
    if not swiped_id or not swipe_type:
        raise ValueError("swiped_id and swipe_type required")
    
    # All swipes must be project-based (project_id is required)
    if not project_id:
        raise ValueError("project_id is required. All swipes must be project-based.")
    
    # Validate project exists, belongs to swiped_id, and is still seeking
    project_check = supabase.table('projects').select('founder_id, seeking_cofounder, is_active').eq('id', project_id).execute()
    if not project_check.data:
        raise ValueError("Project not found")
    elif project_check.data[0]['founder_id'] != swiped_id:
        raise ValueError("Project does not belong to the specified founder")
    elif not project_check.data[0]['is_active']:
        raise ValueError("Project is not active")
    elif not project_check.data[0]['seeking_cofounder']:
        raise ValueError("Project is no longer seeking a co-founder")
    
    # Check if swipe already exists FIRST (before checking limit to avoid wasting swipes)
    existing_swipe_query = supabase.table('swipes').select('id').eq('swiper_id', swiper_id).eq('swiped_id', swiped_id).eq('project_id', project_id)
    
    existing_swipe = existing_swipe_query.execute()
    
    if existing_swipe.data:
        # Return the existing swipe instead of creating a duplicate
        # Don't check limit or increment usage for existing swipes
        return existing_swipe.data[0]
    
    # Check workspace limit for right swipes FIRST (before discovery limit)
    # This prevents users from sending connection requests if they're at their workspace limit
    # Only check if this is a NEW swipe
    if swipe_type == 'right':
        can_create_workspace, current_workspace_count, max_workspaces = plan_service.check_workspace_limit(clerk_user_id)
        if not can_create_workspace:
            raise ValueError(f"Workspace limit reached. You currently have {current_workspace_count} of {max_workspaces} workspaces. Please upgrade your plan or remove existing workspaces before sending new connection requests.")
        
        # Check discovery limit for right swipes (plan-based, not credits)
        # FREE plan: 10 matches/month, PRO/PRO_PLUS: unlimited
        can_swipe, current_count, max_allowed = plan_service.check_discovery_limit(clerk_user_id)
        if not can_swipe:
            if max_allowed == -1:  # Unlimited
                raise ValueError("Unexpected error: Unable to swipe despite unlimited plan")
            raise ValueError(f"Discovery limit reached. You've used {current_count} of {max_allowed} swipes this month. Upgrade to Pro for unlimited discovery.")
        
        # Increment usage after successful check (only for new swipes)
        plan_service.increment_discovery_usage(clerk_user_id)
    
    # Insert swipe (project_id is required - all swipes are project-based)
    swipe_data = {
        'swiper_id': swiper_id,
        'swiped_id': swiped_id,
        'swipe_type': swipe_type,
        'project_id': project_id  # Required - all swipes are project-based
    }
    
    try:
        response = supabase.table('swipes').insert(swipe_data).execute()
    except Exception as e:
        if 'duplicate key value' in str(e):
            # Fetch and return the existing swipe
            existing = supabase.table('swipes').select('*').eq('swiper_id', swiper_id).eq('swiped_id', swiped_id).eq('project_id', project_id).execute()
            if existing.data:
                return existing.data[0]
        raise e
    result = response.data[0]
    
    # No auto-match logic - all matches require explicit approval via respond_to_like()
    # Right swipes create requests that the project owner must approve/decline
    result['match_created'] = False
    return result

