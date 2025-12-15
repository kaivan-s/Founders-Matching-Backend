"""Match-related business logic"""
from config.database import get_supabase

def get_matches(clerk_user_id):
    """Get matches for the current user"""
    supabase = get_supabase()
    
    # Get current user's founder ID
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    current_user_id = user_profile.data[0]['id']
    
    # Get matches where current user is founder1 or founder2
    matches1 = supabase.table('matches').select('*, founder1:founders!founder1_id(*), founder2:founders!founder2_id(*)').eq('founder1_id', current_user_id).execute()
    matches2 = supabase.table('matches').select('*, founder1:founders!founder1_id(*), founder2:founders!founder2_id(*)').eq('founder2_id', current_user_id).execute()
    
    # Combine and format matches
    all_matches = []
    if matches1.data:
        all_matches.extend(matches1.data)
    if matches2.data:
        all_matches.extend(matches2.data)
    
    # Format to show the other founder (not current user) with projects
    formatted_matches = []
    for match in all_matches:
        if match['founder1_id'] == current_user_id:
            other_founder = match.get('founder2') or {}
            my_project_id = match.get('project1_id')
            their_project_id = match.get('project2_id')
        else:
            other_founder = match.get('founder1') or {}
            my_project_id = match.get('project2_id')
            their_project_id = match.get('project1_id')
        
        # Fetch projects for the matched founder
        if other_founder.get('id'):
            founder_id = other_founder['id']
            projects = supabase.table('projects').select('*').eq('founder_id', founder_id).order('display_order').execute()
            other_founder['projects'] = projects.data if projects.data else []
        
            # Fetch specific project information if this is a project-based match
            match_project1 = None
            match_project2 = None
            if their_project_id:
                project_result = supabase.table('projects').select('*').eq('id', their_project_id).execute()
                if project_result.data:
                    match_project2 = project_result.data[0]
            if my_project_id:
                project_result = supabase.table('projects').select('*').eq('id', my_project_id).execute()
                if project_result.data:
                    match_project1 = project_result.data[0]
        
        formatted_matches.append({
            'match_id': match['id'],
            'matched_at': match['created_at'],
                'founder': other_founder,
                'project1': match_project1,  # Current user's project in the match
                'project2': match_project2,  # Other user's project in the match
                'is_project_based': bool(their_project_id or my_project_id)
        })
    
    return formatted_matches

def get_likes(clerk_user_id):
    """Get people who liked you (swiped right on you) but you haven't swiped on them yet"""
    supabase = get_supabase()
    
    # Get current user's founder ID
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    current_user_id = user_profile.data[0]['id']
    
    # Get swipes where someone swiped right on current user
    likes_swipes = supabase.table('swipes').select('*, swiper:founders!swiper_id(*)').eq('swiped_id', current_user_id).eq('swipe_type', 'right').execute()
    
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
    
    # Get matches to filter out already matched (but only for same project combinations)
    matches1 = supabase.table('matches').select('founder1_id, founder2_id, project1_id, project2_id').eq('founder1_id', current_user_id).execute()
    matches2 = supabase.table('matches').select('founder1_id, founder2_id, project1_id, project2_id').eq('founder2_id', current_user_id).execute()
    
    # Track matched user+project combinations
    # Key: (other_user_id, our_project_they_swiped_on_that_we_accepted)
    # When we accept, we create a match with our project (the one they swiped on) and their project
    matched_combinations = set()
    if matches1.data:
        for m in matches1.data:
            other_user_id = m['founder2_id']
            # When we're founder1: project1_id is OUR project, project2_id is THEIR project
            # They swiped on project1_id, so track (other_user, project1_id)
            if m.get('project1_id'):
                matched_combinations.add((other_user_id, m['project1_id']))
            if not m.get('project1_id') and not m.get('project2_id'):
                # Legacy match without projects - exclude user entirely
                matched_combinations.add((other_user_id, None))
    if matches2.data:
        for m in matches2.data:
            other_user_id = m['founder1_id']
            # When we're founder2: project1_id is THEIR project, project2_id is OUR project
            # They swiped on project2_id, so track (other_user, project2_id)
            if m.get('project2_id'):
                matched_combinations.add((other_user_id, m['project2_id']))
            if not m.get('project1_id') and not m.get('project2_id'):
                # Legacy match without projects - exclude user entirely
                matched_combinations.add((other_user_id, None))
    
    # Format likes - exclude already swiped or matched
    formatted_likes = []
    for swipe in likes_swipes.data:
        swiper = swipe.get('swiper') or {}
        swiper_id = swiper.get('id')
        project_id = swipe.get('project_id')  # Project they swiped on (OUR project)
        
        # Skip if already swiped or matched (project-specific check)
        if project_id:
            # Project-based swipe - check if we've already swiped on this specific project
            if (swiper_id, project_id) in swiped_project_combinations:
                continue
            # Check if we've already matched on this specific project combination
            # project_id is OUR project that they swiped on - if we've matched, we accepted it
            if (swiper_id, project_id) in matched_combinations:
                continue
        else:
            # Legacy user-based swipe - check if already swiped or matched on this user (any project)
            if swiper_id in swiped_user_ids:
                continue
            # Check for legacy matches (no projects) - exclude user entirely
            if (swiper_id, None) in matched_combinations:
                continue
        
        # Fetch projects for the swiper
        if swiper_id:
            projects = supabase.table('projects').select('*').eq('founder_id', swiper_id).order('display_order').execute()
            swiper['projects'] = projects.data if projects.data else []
        
        # Fetch the specific project they're interested in (if project-based)
        interested_project = None
        if project_id:
            project_result = supabase.table('projects').select('*').eq('id', project_id).execute()
            if project_result.data:
                interested_project = project_result.data[0]
        
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
        # Create a match
        swipe_project_id = swipe_data.get('project_id')  # Project they swiped on
        
        # Get project information for match creation
        # swipe_project_id = the project of CURRENT USER that the SWIPER swiped on
        # For the match, we need:
        # - current_user_project_id = the project of CURRENT USER (swipe_project_id)
        # - swiper_project_id = the project of SWIPER (their "primary" or first active project)
        current_user_project_id = swipe_project_id  # This is YOUR project they swiped on
        
        # Get the swiper's first active project to use in the match
        swiper_project_id_for_match = None
        if swipe_project_id:
            # Get the swiper's active projects
            swiper_projects = supabase.table('projects').select('id').eq('founder_id', swiper_id).eq('is_active', True).order('created_at', desc=False).execute()
            if swiper_projects.data:
                # Use their first active project as the project they're "offering" in this match
                swiper_project_id_for_match = swiper_projects.data[0]['id']
        
        # Check if match already exists (project-specific if project-based)
        match_exists = False
        existing_match_id = None
        if swipe_project_id and swiper_project_id_for_match:
            # Project-based match - check for specific project combination
            # Get all matches between these two users
            all_matches = supabase.table('matches').select('*').execute()
            if all_matches.data:
                for match in all_matches.data:
                    # Check if same users
                    if ((match['founder1_id'] == current_user_id and match['founder2_id'] == swiper_id) or \
                       (match['founder1_id'] == swiper_id and match['founder2_id'] == current_user_id)):
                        # Check if this exact project combination already exists (order doesn't matter)
                        match_proj1 = match.get('project1_id')
                        match_proj2 = match.get('project2_id')
                        if match_proj1 and match_proj2:
                            # Check if the same two projects are matched (in any order)
                            if ((match_proj1 == current_user_project_id and match_proj2 == swiper_project_id_for_match) or \
                               (match_proj1 == swiper_project_id_for_match and match_proj2 == current_user_project_id)):
                                match_exists = True
                                existing_match_id = match['id']
                                break
        else:
            # Legacy user-based match - check for any match between these users without projects
            all_matches = supabase.table('matches').select('*').execute()
            if all_matches.data:
                for match in all_matches.data:
                    if ((match['founder1_id'] == current_user_id and match['founder2_id'] == swiper_id) or \
                       (match['founder1_id'] == swiper_id and match['founder2_id'] == current_user_id)):
                        # Only consider it a duplicate if it's a legacy match (no projects)
                        if not match.get('project1_id') and not match.get('project2_id'):
                            match_exists = True
                            existing_match_id = match['id']
                            break
        
        # Get or create match
        match_id = None
        if not match_exists:
            match_data = {
                'founder1_id': min(current_user_id, swiper_id),
                'founder2_id': max(current_user_id, swiper_id)
            }
            
            # Add project IDs if this is a project-based match
            if swipe_project_id and swiper_project_id_for_match:
                if current_user_id < swiper_id:
                    match_data['project1_id'] = current_user_project_id
                    match_data['project2_id'] = swiper_project_id_for_match
                else:
                    match_data['project1_id'] = swiper_project_id_for_match
                    match_data['project2_id'] = current_user_project_id
            
            match_result = supabase.table('matches').insert(match_data).execute()
            
            if match_result.data:
                match_id = match_result.data[0]['id']
        else:
            match_id = existing_match_id
        
        # Always ensure workspace exists (for both new and existing matches)
        if match_id:
            from services.workspace_service import create_workspace_for_match
            try:
                workspace_id = create_workspace_for_match(match_id)
            except Exception as e:
                import traceback
                traceback.print_exc()
                # Don't fail the whole operation, but log the error
        
        # Delete the original swipe record since it's been processed (accepted)
        supabase.table('swipes').delete().eq('id', swipe_id).execute()
        
        return {"message": "Match created successfully"}
    else:
        # Reject - record that you swiped left on them and delete the original swipe
        project_id = swipe_data.get('project_id')  # Get project_id from the original swipe
        
        # Check if swipe already exists
        existing_swipe = supabase.table('swipes').select('*').eq('swiper_id', current_user_id).eq('swiped_id', swiper_id).execute()
        
        if not existing_swipe.data:
            swipe_data = {
                'swiper_id': current_user_id,
                'swiped_id': swiper_id,
                'swipe_type': 'left'
            }
            # Include project_id if this was a project-based swipe
            if project_id:
                swipe_data['project_id'] = project_id
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
    
    # Verify user is part of this match
    match = supabase.table('matches').select('*').eq('id', match_id).execute()
    if not match.data:
        raise ValueError("Match not found")
    
    match_data = match.data[0]
    if match_data['founder1_id'] != current_user_id and match_data['founder2_id'] != current_user_id:
        raise ValueError("You are not part of this match")
    
    # Delete the match
    supabase.table('matches').delete().eq('id', match_id).execute()
    
    return {"message": "Match removed successfully"}

