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
    project_id = data.get('project_id')  # New: project being swiped on
    swiper_project_id = data.get('swiper_project_id')  # New: swiper's project for matching
    
    if not swiped_id or not swipe_type:
        raise ValueError("swiped_id and swipe_type required")
    
    # Check discovery limit for right swipes (plan-based, not credits)
    # FREE plan: 10 matches/month, PRO/PRO_PLUS: unlimited
    if swipe_type == 'right':
        can_swipe, current_count, max_allowed = plan_service.check_discovery_limit(clerk_user_id)
        if not can_swipe:
            if max_allowed == -1:  # Unlimited
                raise ValueError("Unexpected error: Unable to swipe despite unlimited plan")
            raise ValueError(f"Discovery limit reached. You've used {current_count} of {max_allowed} swipes this month. Upgrade to Pro for unlimited discovery.")
        
        # Increment usage after successful check
        plan_service.increment_discovery_usage(clerk_user_id)
    
    # Check if swipe already exists to avoid constraint violation
    existing_swipe_query = supabase.table('swipes').select('id').eq('swiper_id', swiper_id).eq('swiped_id', swiped_id)
    
    # For project-based swipes, check the specific project
    if project_id:
        existing_swipe_query = existing_swipe_query.eq('project_id', project_id)
    else:
        # For legacy swipes, check where project_id is null
        existing_swipe_query = existing_swipe_query.is_('project_id', None)
    
    existing_swipe = existing_swipe_query.execute()
    
    if existing_swipe.data:
        # Return the existing swipe instead of creating a duplicate
        return existing_swipe.data[0]
    
    # Insert swipe (with optional project_id for project-based swiping)
    swipe_data = {
        'swiper_id': swiper_id,
        'swiped_id': swiped_id,
        'swipe_type': swipe_type
    }
    
    # Add project_id if this is a project-based swipe
    if project_id:
        swipe_data['project_id'] = project_id
    
    try:
        response = supabase.table('swipes').insert(swipe_data).execute()
    except Exception as e:
        if 'duplicate key value' in str(e):
            # Fetch and return the existing swipe
            existing = supabase.table('swipes').select('*').eq('swiper_id', swiper_id).eq('swiped_id', swiped_id).execute()
            if existing.data:
                return existing.data[0]
        raise e
    result = response.data[0]
    
    # Check for mutual swipe and create match automatically (only for right swipes)
    match_created = False
    if swipe_type == 'right':
        # For project-based matching
        if project_id and swiper_project_id:
            # Check if the other person has swiped right on the swiper's project
            mutual_swipe_query = supabase.table('swipes').select('*').eq('swiper_id', swiped_id).eq('swiped_id', swiper_id).eq('swipe_type', 'right')
            
            # If we're doing project-based matching, also check the project
            if swiper_project_id:
                mutual_swipe_query = mutual_swipe_query.eq('project_id', swiper_project_id)
            
            mutual_swipe = mutual_swipe_query.execute()
        else:
            # Legacy user-to-user swipe
            mutual_swipe = supabase.table('swipes').select('*').eq('swiper_id', swiped_id).eq('swiped_id', swiper_id).eq('swipe_type', 'right').execute()
        
        if mutual_swipe.data:
            # Both users have swiped right - create a match automatically!
            
            # Check if match already exists for these specific projects
            # This allows same users to have multiple matches for different project combinations
            match_exists = False
            if project_id and swiper_project_id:
                # Project-based match - check for specific project combination using database query
                # Query for matches between these two users (using consistent ordering)
                # Then check project combinations in Python (much better than fetching ALL matches)
                ordered_founder1 = min(swiper_id, swiped_id)
                ordered_founder2 = max(swiper_id, swiped_id)
                
                # Query only matches between these two users
                matches_query = supabase.table('matches').select('project1_id, project2_id').eq('founder1_id', ordered_founder1).eq('founder2_id', ordered_founder2).execute()
                
                # Check if any match has the same project combination (order doesn't matter)
                if matches_query.data:
                    for match in matches_query.data:
                            match_proj1 = match.get('project1_id')
                            match_proj2 = match.get('project2_id')
                            if match_proj1 and match_proj2:
                            # Check if same two projects (in any order)
                                if ((match_proj1 == swiper_project_id and match_proj2 == project_id) or \
                                   (match_proj1 == project_id and match_proj2 == swiper_project_id)):
                                    match_exists = True
                                    break
            else:
                # Legacy user-based match - check for any match between these users (without projects)
                # Use database query to filter at database level with consistent ordering
                ordered_founder1 = min(swiper_id, swiped_id)
                ordered_founder2 = max(swiper_id, swiped_id)
                
                matches_query = supabase.table('matches').select('id').eq('founder1_id', ordered_founder1).eq('founder2_id', ordered_founder2).is_('project1_id', None).is_('project2_id', None).execute()
                
                match_exists = bool(matches_query.data and len(matches_query.data) > 0)
            
            if not match_exists:
                # Create match with consistent ordering (smaller ID first)
                match_data = {
                    'founder1_id': min(swiper_id, swiped_id),
                    'founder2_id': max(swiper_id, swiped_id)
                }
                
                # Add project IDs if this is a project-based match
                if project_id and swiper_project_id:
                    # Order projects based on founder ordering
                    if swiper_id < swiped_id:
                        match_data['project1_id'] = swiper_project_id
                        match_data['project2_id'] = project_id
                    else:
                        match_data['project1_id'] = project_id
                        match_data['project2_id'] = swiper_project_id
                
                match_result = supabase.table('matches').insert(match_data).execute()
                
                # Auto-create workspace for the new match
                if match_result.data:
                    from services.workspace_service import create_workspace_for_match
                    match_id = match_result.data[0]['id']
                    try:
                        create_workspace_for_match(match_id)
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                
                match_created = True
    
    # Add match_created flag to response
    result['match_created'] = match_created
    return result

