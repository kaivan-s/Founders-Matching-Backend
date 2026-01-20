"""Match-related business logic"""
from datetime import datetime, timezone, timedelta
from config.database import get_supabase

def get_matches(clerk_user_id):
    """Get matches for the current user (excludes expired matches)"""
    supabase = get_supabase()
    
    # Get current user's founder ID
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    current_user_id = user_profile.data[0]['id']
    
    # Check and mark expired matches
    _check_and_mark_expired_matches()
    
    # Get matches where current user is founder1 or founder2 (exclude expired)
    # Using PostgREST filter syntax: or=(condition1,condition2)
    try:
        # Try using OR filter syntax (PostgREST format: or=(field.eq.value,field.eq.value))
        all_matches_result = supabase.table('matches').select(
            '*, founder1:founders!founder1_id(*), founder2:founders!founder2_id(*)'
        ).or_(f'founder1_id.eq.{current_user_id},founder2_id.eq.{current_user_id}').eq('is_expired', False).execute()
        all_matches = all_matches_result.data if all_matches_result.data else []
    except (AttributeError, Exception) as e:
        # Fallback to two queries if OR syntax not supported by client
        # This is still more efficient than before as we combine results immediately
        matches1 = supabase.table('matches').select('*, founder1:founders!founder1_id(*), founder2:founders!founder2_id(*)').eq('founder1_id', current_user_id).eq('is_expired', False).execute()
        matches2 = supabase.table('matches').select('*, founder1:founders!founder1_id(*), founder2:founders!founder2_id(*)').eq('founder2_id', current_user_id).eq('is_expired', False).execute()
        all_matches = []
        if matches1.data:
            all_matches.extend(matches1.data)
        if matches2.data:
            all_matches.extend(matches2.data)
    
    # Batch fetch all projects to avoid N+1 queries
    # Collect all founder IDs and project IDs
    founder_ids = set()
    project_ids = set()
    for match in all_matches:
        if match.get('founder1_id'):
            founder_ids.add(match['founder1_id'])
        if match.get('founder2_id'):
            founder_ids.add(match['founder2_id'])
        if match.get('project_id'):
            project_ids.add(match['project_id'])
    
    # Batch fetch all projects for all founders
    founder_projects_map = {}
    if founder_ids:
        all_founder_projects = supabase.table('projects').select('*').in_('founder_id', list(founder_ids)).order('display_order').execute()
        if all_founder_projects.data:
            for project in all_founder_projects.data:
                founder_id = project['founder_id']
                if founder_id not in founder_projects_map:
                    founder_projects_map[founder_id] = []
                founder_projects_map[founder_id].append(project)
    
    # Batch fetch all match projects
    match_projects_map = {}
    if project_ids:
        all_match_projects = supabase.table('projects').select('*').in_('id', list(project_ids)).execute()
        if all_match_projects.data:
            for project in all_match_projects.data:
                match_projects_map[project['id']] = project
    
    # Format to show the other founder (not current user) with projects
    # Only return project-based matches (no founder-level matches)
    formatted_matches = []
    for match in all_matches:
        match_project_id = match.get('project_id')
        
        # Skip legacy matches without project_id (no longer supported)
        if not match_project_id:
            continue
        
        if match['founder1_id'] == current_user_id:
            other_founder = match.get('founder2') or {}
        else:
            other_founder = match.get('founder1') or {}
        
        # Assign projects for the matched founder (from batch fetch)
        if other_founder.get('id'):
            founder_id = other_founder['id']
            other_founder['projects'] = founder_projects_map.get(founder_id, [])
        
        # Get the specific project that was matched (from batch fetch)
        match_project = match_projects_map.get(match_project_id)
        
        # Get compatibility score if available
        compatibility_score = None
        try:
            from services.compatibility_service import get_compatibility_score
            compatibility_score = get_compatibility_score(match['id'])
        except Exception:
            pass
        
        formatted_matches.append({
            'match_id': match['id'],
            'matched_at': match['created_at'],
            'expires_at': match.get('expires_at'),
            'is_expired': match.get('is_expired', False),
            'founder': other_founder,
            'project': match_project,  # The project they matched on (one project, two founders)
            'is_project_based': True,  # All matches are project-based
            'compatibility_score': compatibility_score  # Compatibility breakdown
        })
    
    return formatted_matches

def _check_and_mark_expired_matches():
    """Check and mark expired matches (30 days with no workspace activity)"""
    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)
    
    # Get matches that are expired but not marked
    expired_matches = supabase.table('matches').select('id, expires_at').eq('is_expired', False).lt('expires_at', now.isoformat()).execute()
    
    if expired_matches.data:
        match_ids = [m['id'] for m in expired_matches.data]
        
        # Check if workspaces have activity in last 30 days
        for match_id in match_ids:
            # Get workspace for this match
            workspace = supabase.table('workspaces').select('id, updated_at').eq('match_id', match_id).execute()
            
            if workspace.data:
                workspace_updated = workspace.data[0].get('updated_at')
                if workspace_updated:
                    try:
                        if isinstance(workspace_updated, str):
                            from dateutil import parser
                            workspace_updated = parser.parse(workspace_updated)
                        if isinstance(workspace_updated, datetime):
                            days_since_update = (now - workspace_updated.replace(tzinfo=timezone.utc)).days
                            if days_since_update < 30:
                                continue  # Workspace has recent activity, don't expire
                    except Exception:
                        pass  # If date parsing fails, proceed with expiration
            
            # Mark as expired
            supabase.table('matches').update({'is_expired': True}).eq('id', match_id).execute()

def get_likes(clerk_user_id):
    """Get people who liked you (swiped right on you) but you haven't swiped on them yet"""
    supabase = get_supabase()
    
    # Get current user's founder ID
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    current_user_id = user_profile.data[0]['id']
    
    # Get swipes where someone swiped right on current user
    # Only include swipes for projects that are still seeking co-founders
    # Join with projects table to filter by seeking_cofounder status
    likes_swipes = supabase.table('swipes').select(
        '*, swiper:founders!swiper_id(*), project:projects!project_id(id, seeking_cofounder, is_active)'
    ).eq('swiped_id', current_user_id).eq('swipe_type', 'right').execute()
    
    if not likes_swipes.data:
        return []
    
    # Filter out swipes for projects that are no longer seeking or not active
    # This handles cases where project was matched or deleted after the swipe
    filtered_likes = []
    for swipe in likes_swipes.data:
        project = swipe.get('project')
        # Only include if project exists, is active, and still seeking
        if project and project.get('is_active', True) and project.get('seeking_cofounder', True):
            filtered_likes.append(swipe)
    
    likes_swipes.data = filtered_likes
    
    if not likes_swipes.data:
        return []
    
    # Get swipes by current user to filter out already swiped (project-specific for project-based swipes)
    my_swipes = supabase.table('swipes').select('swiped_id, project_id').eq('swiper_id', current_user_id).execute()
    swiped_user_ids = set()
    swiped_project_combinations = set()  # Track (user_id, project_id) combinations
    
    if my_swipes.data:
        for swipe in my_swipes.data:
            swiped_user_ids.add(swipe['swiped_id'])
            if swipe.get('project_id'):
                swiped_project_combinations.add((swipe['swiped_id'], swipe['project_id']))
    
    # Get matches to filter out already matched (one project, two founders)
    matches1 = supabase.table('matches').select('founder1_id, founder2_id, project_id').eq('founder1_id', current_user_id).execute()
    matches2 = supabase.table('matches').select('founder1_id, founder2_id, project_id').eq('founder2_id', current_user_id).execute()
    
    # Track matched user+project combinations
    # Key: (other_user_id, project_id) - the project they matched on
    # Only project-based matches are supported (no founder-level matches)
    matched_combinations = set()
    if matches1.data:
        for m in matches1.data:
            other_user_id = m['founder2_id']
            project_id = m.get('project_id')
            if project_id:
                # Only include project-based matches
                matched_combinations.add((other_user_id, project_id))
            # Legacy matches without project_id are ignored (no longer supported)
    if matches2.data:
        for m in matches2.data:
            other_user_id = m['founder1_id']
            project_id = m.get('project_id')
            if project_id:
                # Only include project-based matches
                matched_combinations.add((other_user_id, project_id))
            # Legacy matches without project_id are ignored (no longer supported)
    
    # Batch fetch all projects to avoid N+1 queries
    # Collect all swiper IDs and project IDs
    swiper_ids = set()
    project_ids = set()
    for swipe in likes_swipes.data:
        swiper = swipe.get('swiper') or {}
        swiper_id = swiper.get('id')
        project_id = swipe.get('project_id')
        
        if swiper_id:
            swiper_ids.add(swiper_id)
        if project_id:
            project_ids.add(project_id)
    
    # Batch fetch all projects for all swipers
    swiper_projects_map = {}
    if swiper_ids:
        all_swiper_projects = supabase.table('projects').select('*').in_('founder_id', list(swiper_ids)).order('display_order').execute()
        if all_swiper_projects.data:
            for project in all_swiper_projects.data:
                founder_id = project['founder_id']
                if founder_id not in swiper_projects_map:
                    swiper_projects_map[founder_id] = []
                swiper_projects_map[founder_id].append(project)
    
    # Batch fetch all interested projects
    interested_projects_map = {}
    if project_ids:
        all_interested_projects = supabase.table('projects').select('*').in_('id', list(project_ids)).execute()
        if all_interested_projects.data:
            for project in all_interested_projects.data:
                interested_projects_map[project['id']] = project
    
    # Format likes - exclude already swiped or matched
    formatted_likes = []
    for swipe in likes_swipes.data:
        swiper = swipe.get('swiper') or {}
        swiper_id = swiper.get('id')
        project_id = swipe.get('project_id')  # Project they swiped on (OUR project)
        
        # Skip if already swiped or matched (project-specific check)
        # All swipes must be project-based (no founder-level swipes)
        if project_id:
            # Project-based swipe - check if we've already swiped on this specific project
            if (swiper_id, project_id) in swiped_project_combinations:
                continue
            # Check if we've already matched on this specific project combination
            # project_id is OUR project that they swiped on - if we've matched, we accepted it
            if (swiper_id, project_id) in matched_combinations:
                continue
        else:
            # Legacy swipe without project_id - filter out (no longer supported)
            # These should not appear in new swipes, but handle gracefully for existing data
            continue
        
        # Assign projects for the swiper (from batch fetch)
        if swiper_id:
            swiper['projects'] = swiper_projects_map.get(swiper_id, [])
        
        # Get the specific project they're interested in (from batch fetch)
        interested_project = interested_projects_map.get(project_id) if project_id else None
        
        formatted_likes.append({
            'swipe_id': swipe['id'],
            'liked_at': swipe['created_at'],
            'founder': swiper,
            'project': interested_project,  # The project they're interested in
            'project_id': project_id,  # Project ID they swiped on
            'is_project_based': bool(project_id)
        })
    
    return formatted_likes

def respond_to_like(clerk_user_id, swipe_id, response_type):
    """Respond to a like - accept (creates match) or reject"""
    supabase = get_supabase()
    
    if response_type not in ['accept', 'reject']:
        raise ValueError("response must be 'accept' or 'reject'")
    
    # Get current user's founder ID
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    current_user_id = user_profile.data[0]['id']
    
    # Get the swipe record
    swipe = supabase.table('swipes').select('*, swiper:founders!swiper_id(id)').eq('id', swipe_id).eq('swiped_id', current_user_id).execute()
    
    if not swipe.data:
        raise ValueError("Like not found")
    
    swipe_data = swipe.data[0]
    swiper_id = swipe_data.get('swiper', {}).get('id') if isinstance(swipe_data.get('swiper'), dict) else None
    
    if not swiper_id:
        # Try to get swiper_id directly
        swiper_id = swipe_data.get('swiper_id')
    
    if response_type == 'accept':
        # Create a match - project-based only (no founder-level matches)
        swipe_project_id = swipe_data.get('project_id')  # Project they swiped on (YOUR project)
        
        # Project ID is REQUIRED - all matches are project-based
        if not swipe_project_id:
            raise ValueError("Project ID is required. All matches must be project-based.")
        
        project_id = swipe_project_id  # This is the project they matched on
        
        # Check if match already exists for this project between these founders
        ordered_founder1 = min(current_user_id, swiper_id)
        ordered_founder2 = max(current_user_id, swiper_id)
        
        match_exists = False
        existing_match_id = None
        
        # Project-based match - check for this specific project
        matches_query = supabase.table('matches').select('id').eq('founder1_id', ordered_founder1).eq('founder2_id', ordered_founder2).eq('project_id', project_id).execute()
        if matches_query.data:
            match_exists = True
            existing_match_id = matches_query.data[0]['id']
        
        # Validate project owner, status, and seeking status before creating match
        project_check = supabase.table('projects').select('founder_id, seeking_cofounder, is_active, is_deleted, visibility_level').eq('id', project_id).execute()
        if not project_check.data:
            raise ValueError("Project not found")
        
        project = project_check.data[0]
        
        if project['founder_id'] != current_user_id:
            raise ValueError("Project does not belong to you")
        
        if project.get('is_deleted', False):
            raise ValueError("Cannot create match: Project has been deleted")
        
        if not project.get('is_active', True):
            raise ValueError("Cannot create match: Project is not active")
        
        if not project.get('seeking_cofounder', True):
            raise ValueError("Cannot create match: Project is no longer seeking a co-founder")
        
        # Check if founder is inactive (hasn't been active in 90 days)
        founder_check = supabase.table('founders').select('is_active, last_active_at').eq('id', swiper_id).execute()
        if founder_check.data:
            founder = founder_check.data[0]
            if not founder.get('is_active', True):
                raise ValueError("Cannot create match: The other founder appears to be inactive")
            
            # Check if last_active_at is more than 90 days ago
            from datetime import datetime, timezone, timedelta
            last_active = founder.get('last_active_at')
            if last_active:
                try:
                    if isinstance(last_active, str):
                        from dateutil import parser
                        last_active = parser.parse(last_active)
                    if isinstance(last_active, datetime):
                        days_inactive = (datetime.now(timezone.utc) - last_active.replace(tzinfo=timezone.utc)).days
                        if days_inactive > 90:
                            raise ValueError("Cannot create match: The other founder has been inactive for over 90 days")
                except Exception:
                    # If date parsing fails, allow the match (graceful degradation)
                    pass
        
        # Get or create match (project-based only)
        match_id = None
        if not match_exists:
            # Create project-based match (one project, two founders)
            match_data = {
                'founder1_id': min(current_user_id, swiper_id),
                'founder2_id': max(current_user_id, swiper_id),
                'project_id': project_id  # Required - all matches are project-based
            }
            
            match_result = supabase.table('matches').insert(match_data).execute()
            
            if match_result.data:
                match_id = match_result.data[0]['id']
                
                # Calculate and save compatibility score
                try:
                    from services.compatibility_service import save_compatibility_score
                    save_compatibility_score(match_id, current_user_id, swiper_id, project_id)
                except Exception as e:
                    # Log but don't fail match creation
                    from utils.logger import log_error
                    log_error(f"Failed to calculate compatibility score for match {match_id}", error=e)
                
                # Create workspace FIRST, then mark project (atomicity)
                try:
                    from services.workspace_service import create_workspace_for_match
                    create_workspace_for_match(match_id)
                    
                    # Only mark project AFTER workspace is created successfully
                    try:
                        supabase.table('projects').update({'seeking_cofounder': False}).eq('id', project_id).execute()
                    except Exception as e:
                        # Log but don't fail - workspace exists, project state is secondary
                        from utils.logger import log_error
                        log_error(f"Failed to mark project as matched for match {match_id}", error=e)
                except Exception as e:
                    # Workspace creation failed - rollback match creation
                    from utils.logger import log_error
                    log_error(f"Workspace creation failed for match {match_id}, rolling back match", error=e)
                    try:
                        supabase.table('matches').delete().eq('id', match_id).execute()
                    except Exception as rollback_error:
                        log_error(f"Failed to rollback match {match_id} after workspace creation failure", error=rollback_error)
                    raise ValueError("Failed to create workspace for match")
        else:
            match_id = existing_match_id
            # Mark project even for existing match (data consistency)
            try:
                supabase.table('projects').update({'seeking_cofounder': False}).eq('id', project_id).execute()
            except Exception as e:
                # Log but don't fail
                from utils.logger import log_error
                log_error(f"Failed to mark project as matched for existing match {match_id}", error=e)
        
        # Always ensure workspace exists (for both new and existing matches)
        if match_id:
            from services.workspace_service import create_workspace_for_match
            try:
                workspace_id = create_workspace_for_match(match_id)
            except Exception as e:
                # For existing matches, workspace might already exist, so don't fail
                from utils.logger import log_error
                log_error(f"Workspace creation/check failed for existing match {match_id}", error=e)
        
        # Delete the original swipe record since it's been processed (accepted)
        supabase.table('swipes').delete().eq('id', swipe_id).execute()
        
        return {"message": "Match created successfully"}
    else:
        # Reject - record that you swiped left on them and delete the original swipe
        project_id = swipe_data.get('project_id')  # Get project_id from the original swipe
        
        # Project ID is required (all swipes are project-based)
        if not project_id:
            raise ValueError("Project ID is required. All swipes must be project-based.")
        
        # Check if swipe already exists
        existing_swipe = supabase.table('swipes').select('*').eq('swiper_id', current_user_id).eq('swiped_id', swiper_id).eq('project_id', project_id).execute()
        
        if not existing_swipe.data:
            swipe_data = {
                'swiper_id': current_user_id,
                'swiped_id': swiper_id,
                'swipe_type': 'left',
                'project_id': project_id  # Required - all swipes are project-based
            }
            supabase.table('swipes').insert(swipe_data).execute()
        
        # Delete the original swipe record since it's been processed (rejected)
        supabase.table('swipes').delete().eq('id', swipe_id).execute()
        
        return {"message": "Like rejected"}

def unmatch(clerk_user_id, match_id):
    """Remove a match (unmatch)"""
    supabase = get_supabase()
    
    # Get current user's founder ID
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    current_user_id = user_profile.data[0]['id']
    
    # Verify user is part of this match and get match data
    match = supabase.table('matches').select('*').eq('id', match_id).execute()
    if not match.data:
        raise ValueError("Match not found")
    
    match_data = match.data[0]
    if match_data['founder1_id'] != current_user_id and match_data['founder2_id'] != current_user_id:
        raise ValueError("You are not part of this match")
    
    # Get project_id before deleting match (for restoring project state)
    project_id = match_data.get('project_id')
    
    # Delete associated workspace first (if exists) - foreign key constraint
    try:
        workspace = supabase.table('workspaces').select('id').eq('match_id', match_id).execute()
        if workspace.data:
            workspace_id = workspace.data[0]['id']
            # Delete workspace participants first (foreign key constraint)
            supabase.table('workspace_participants').delete().eq('workspace_id', workspace_id).execute()
            # Delete workspace
            supabase.table('workspaces').delete().eq('id', workspace_id).execute()
    except Exception as e:
        from utils.logger import log_error
        log_error(f"Failed to delete workspace after unmatch {match_id}", error=e)
        # Continue with match deletion even if workspace deletion fails
    
    # Delete the match
    supabase.table('matches').delete().eq('id', match_id).execute()
    
    # Restore project state if it was project-based (mark as seeking co-founder again)
    if project_id:
        try:
            supabase.table('projects').update({'seeking_cofounder': True}).eq('id', project_id).execute()
        except Exception as e:
            from utils.logger import log_error
            log_error(f"Failed to restore project state after unmatch {match_id}", error=e)
            # Don't fail the whole operation, but log the error
    
    return {"message": "Match removed successfully"}

