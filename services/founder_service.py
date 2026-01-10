"""Founder-related business logic"""
import json
import traceback
from config.database import get_supabase


def apply_filters_to_founders(founders, filters):
    """
    Apply filters to the list of founders
    
    Args:
        founders: List of founder dictionaries
        filters: Dictionary with filter criteria
    
    Returns:
        Filtered list of founders
    """
    filtered = founders
    
    # Filter by skills (any match)
    if filters.get('skills') and len(filters['skills']) > 0:
        filtered = [
            f for f in filtered 
            if any(skill in (f.get('skills') or []) for skill in filters['skills'])
        ]
    
    # Filter by location (case-insensitive partial match)
    if filters.get('location'):
        location_query = filters['location'].lower()
        filtered = [
            f for f in filtered 
            if location_query in (f.get('location') or '').lower()
        ]
    
    # Filter by project stage (any project matching the stage)
    if filters.get('project_stage'):
        stage = filters['project_stage']
        filtered = [
            f for f in filtered 
            if any(p.get('stage') == stage for p in (f.get('projects') or []))
        ]
    
    # Filter by looking_for (partial match)
    if filters.get('looking_for'):
        looking_for_query = filters['looking_for'].lower()
        filtered = [
            f for f in filtered 
            if looking_for_query in (f.get('looking_for') or '').lower()
        ]
    
    # Text search in name, looking_for, and project titles/descriptions
    if filters.get('search'):
        search_query = filters['search'].lower()
        filtered = [
            f for f in filtered 
            if (
                search_query in (f.get('name') or '').lower() or
                search_query in (f.get('looking_for') or '').lower() or
                any(search_query in (p.get('title') or '').lower() for p in (f.get('projects') or [])) or
                any(search_query in (p.get('description') or '').lower() for p in (f.get('projects') or []))
            )
        ]
    
    return filtered

def calculate_preference_score(preferences, project_answers):
    """Calculate compatibility score based on user preferences and project answers
    Returns a score between 0 and 100
    """
    if not preferences or not any(preferences.values()):
        return 50  # Default neutral score
    
    if not project_answers:
        return 25  # Low score if project has no answers
    
    # Weight for each preference question
    PREFERENCE_WEIGHTS = {
        'primary_role': 30,
        'ideal_outcome': 25,
        'work_hours': 25,
        'work_model': 20
    }
    
    total_score = 0
    total_weight = 0
    
    for pref_id, weight in PREFERENCE_WEIGHTS.items():
        user_pref = preferences.get(pref_id)
        project_answer = project_answers.get(pref_id)
        
        if user_pref and project_answer:
            # Exact match gets full points
            if user_pref == project_answer:
                total_score += weight
            # Partial matches for flexible options
            elif pref_id == 'work_hours' and project_answer == 'flexible':
                # Flexible work hours is partially compatible with any preference
                total_score += weight * 0.5
            elif pref_id == 'ideal_outcome' and user_pref == 'flexible':
                # User is flexible on outcome
                total_score += weight * 0.5
            
            total_weight += weight
    
    # Consider other compatibility questions with lower weight
    # Check if there are matching answers in other fields
    secondary_matches = 0
    secondary_total = 0
    
    for key in project_answers:
        if key not in PREFERENCE_WEIGHTS:
            project_val = project_answers.get(key)
            user_val = preferences.get(key)
            if project_val and user_val:
                secondary_total += 1
                if project_val == user_val:
                    secondary_matches += 1
    
    # Add secondary score with lower weight (10% of total)
    if secondary_total > 0:
        secondary_score = (secondary_matches / secondary_total) * 10
        total_score += secondary_score
        total_weight += 10
    
    # Calculate final percentage
    if total_weight > 0:
        return int(min(100, (total_score / total_weight) * 100))
    
    return 50  # Default score if no matching criteria

def get_available_founders(clerk_user_id, filters=None, mode='founders'):
    """Get founders available for swiping (excludes current user and already swiped)
    Now supports two modes:
    - 'founders': Legacy mode showing all founders (backward compatible)
    - 'projects': New mode showing individual projects as cards
    """
    import json
    supabase = get_supabase()
    
    # Get current user's founder profile
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found. Please create your profile first.")
    
    current_user_id = user_profile.data[0]['id']
    
    # Parse preferences from filters if provided
    user_preferences = None
    if filters and filters.get('preferences'):
        try:
            user_preferences = json.loads(filters['preferences'])
        except Exception as e:
            user_preferences = None
    
    if mode == 'projects':
        # Optimized project-based discovery with single query
        # Use raw SQL for better performance at scale
        
        # Get pagination parameters
        # When preferences are set, fetch more projects initially to find best matches
        # When no preferences, use normal pagination
        final_limit = filters.get('limit', 20) if filters else 20
        offset = filters.get('offset', 0) if filters else 0
        
        # If preferences are set, fetch more candidates (100-200) before preference scoring
        # This ensures we find the best matches, not just the first 20
        if user_preferences and any(user_preferences.values()):
            # Fetch more candidates for preference-based matching
            initial_fetch_limit = min(200, max(100, final_limit * 5))  # Fetch 5x more, max 200
        else:
            # No preferences: use normal pagination
            initial_fetch_limit = final_limit
        
        # Build the optimized query using Supabase's query builder with proper JOINs
        # This single query excludes:
        # 1. User's own projects
        # 2. Projects with existing workspaces 
        # 3. Projects already swiped on by current user
        
        try:
            query = supabase.table('projects').select(
                '*, founder:founders!founder_id(*)'
            ).neq('founder_id', current_user_id).eq('is_active', True).eq('seeking_cofounder', True)
        except Exception as e:
            raise
        
        # Apply filters if provided
        if filters.get('search'):
            search_term = f"%{filters['search']}%"
            # Note: Supabase doesn't support OR in query builder easily, 
            # so we'll filter by title for now (can be optimized with full-text search later)
            query = query.ilike('title', search_term)
        
        if filters.get('project_stage'):
            query = query.eq('stage', filters['project_stage'])
            
        if filters.get('genre'):
            query = query.eq('genre', filters['genre'])
        
        # Order by most recent and fetch initial batch
        # When preferences are set, we fetch more to score them all
        query = query.order('created_at', desc=True).range(offset, offset + initial_fetch_limit - 1)
        
        all_projects = query.execute()
        
        
        if not all_projects.data:
            available_projects = []
        else:
            # Get project IDs for efficient filtering
            project_ids = [p['id'] for p in all_projects.data]
            
            # Optimized: Single query to get workspace project_ids (after migration adds project_id to workspaces)
            # Also get matched project_ids from matches table (defense in depth)
            workspace_project_ids = set()
            matched_project_ids = set()
            if project_ids:
                # Try direct query on workspaces.project_id first (after migration)
                try:
                    workspaces_direct = supabase.table('workspaces').select('project_id').in_('project_id', project_ids).execute()
                    if workspaces_direct.data:
                        for wp in workspaces_direct.data:
                            if wp.get('project_id'):
                                workspace_project_ids.add(wp['project_id'])
                except Exception:
                    # Fallback: If project_id column doesn't exist yet, use match_id lookup
                    matches = supabase.table('matches').select('id, project_id').in_('project_id', project_ids).execute()
                    if matches.data:
                        match_ids = [m['id'] for m in matches.data]
                        # Extract matched project IDs (defense in depth - even if workspace is deleted, match still exists)
                        for m in matches.data:
                            if m.get('project_id'):
                                matched_project_ids.add(m['project_id'])
                        
                        # Check which matches have workspaces
                        if match_ids:
                            workspaces = supabase.table('workspaces').select('match_id').in_('match_id', match_ids).execute()
                            if workspaces.data:
                                # Map workspace match_ids back to project_ids
                                match_to_project = {m['id']: m['project_id'] for m in matches.data if m.get('project_id')}
                                for wp in workspaces.data:
                                    match_id = wp.get('match_id')
                                    if match_id and match_id in match_to_project:
                                        workspace_project_ids.add(match_to_project[match_id])
                
                # Get matched project_ids from matches table (defense in depth - even if workspace is deleted, match still exists)
                # Only if we didn't already get them in the fallback
                if not matched_project_ids:
                    matches = supabase.table('matches').select('project_id').in_('project_id', project_ids).execute()
                    if matches.data:
                        for m in matches.data:
                            if m.get('project_id'):
                                matched_project_ids.add(m['project_id'])
            
            # Single query to get swipes by current user (only for current batch)
            # Only query if we have valid project IDs
            # Only filter RIGHT swipes - allow re-swiping after left swipe
            swiped_combinations = set()
            if project_ids:
                user_swipes = supabase.table('swipes').select('project_id, swiped_id, swipe_type').eq('swiper_id', current_user_id).in_('project_id', project_ids).execute()
                if user_swipes.data:
                    for swipe in user_swipes.data:
                        # Only filter if it was a right swipe (allow re-swiping after left swipe)
                        if swipe.get('project_id') and swipe.get('swipe_type') == 'right':
                            swiped_combinations.add((swipe['project_id'], swipe['swiped_id']))
            
            
            # Filter the batch efficiently
            available_projects = []
            for project in all_projects.data:
                project_id = project['id']
                project_owner_id = project['founder_id']
                
                # Skip if project has workspace, has match, or already right-swiped
                if (project_id not in workspace_project_ids and 
                    project_id not in matched_project_ids and
                    (project_id, project_owner_id) not in swiped_combinations):
                    available_projects.append(project)
        
        
        # Apply project-level filters (skills, location, looking_for)
        if filters:
            filtered_projects = []
            for project in available_projects:
                founder_info = project.get('founder', {})
                founder_skills = founder_info.get('skills', [])
                founder_location = founder_info.get('location', '')
                founder_looking_for = founder_info.get('looking_for', '')
                
                # Filter by skills (founder's skills)
                if filters.get('skills') and len(filters['skills']) > 0:
                    if not any(skill in founder_skills for skill in filters['skills']):
                        continue
                
                # Filter by location
                if filters.get('location'):
                    location_query = filters['location'].lower()
                    if location_query not in founder_location.lower():
                        continue
                
                # Filter by looking_for
                if filters.get('looking_for'):
                    looking_for_query = filters['looking_for'].lower()
                    if looking_for_query not in founder_looking_for.lower():
                        continue
                
                # Filter by search (project title/description or founder name)
                if filters.get('search'):
                    search_query = filters['search'].lower()
                    project_title = (project.get('title') or '').lower()
                    project_desc = (project.get('description') or '').lower()
                    founder_name = (founder_info.get('name') or '').lower()
                    if (search_query not in project_title and 
                        search_query not in project_desc and 
                        search_query not in founder_name):
                        continue
                
                filtered_projects.append(project)
            
            available_projects = filtered_projects
        
        # Format projects as founder-like objects for UI compatibility
        # Each project becomes a separate card
        formatted_results = []
        for project in available_projects:
            founder_info = project.get('founder', {})
            
            # Calculate preference score if user has set preferences
            preference_score = None
            if user_preferences and any(user_preferences.values()):
                project_answers = project.get('compatibility_answers', {})
                preference_score = calculate_preference_score(user_preferences, project_answers)
            
            # Calculate information completeness (not "quality" - just how much info is available)
            # This helps prioritize projects that are easier to evaluate
            info_completeness = 0
            # Description length (more detailed = easier to understand the project)
            description = project.get('description', '')
            if description:
                word_count = len(description.split())
                if word_count >= 50:
                    info_completeness += 1  # Well-detailed
                elif word_count >= 20:
                    info_completeness += 0.5  # Adequate
            
            # Needed skills (helps founders know if they're a good fit)
            # Note: Compatibility answers are compulsory, so not used for differentiation
            if project.get('needed_skills') and len(project.get('needed_skills', [])) > 0:
                info_completeness += 0.5
            
            # Use project ID as the unique identifier
            # This avoids the composite ID issue that was causing UUID parsing errors
            formatted_result = {
                'id': project['id'],  # Use project ID as unique ID
                'founder_id': founder_info.get('id'),  # Keep original founder ID for swiping
                'name': founder_info.get('name'),
                'email': founder_info.get('email'),
                'location': founder_info.get('location'),
                'skills': founder_info.get('skills', []),
                'looking_for': founder_info.get('looking_for'),
                'profile_picture_url': founder_info.get('profile_picture_url'),
                'linkedin_url': founder_info.get('linkedin_url'),
                'website_url': founder_info.get('website_url'),
                'projects': [{
                    'id': project.get('id'),
                    'title': project.get('title', 'Untitled Project'),
                    'description': project.get('description', 'No description available'),
                    'stage': project.get('stage', 'Unknown'),
                    'genre': project.get('genre'),
                    'needed_skills': project.get('needed_skills', []),
                    'compatibility_answers': project.get('compatibility_answers', {}),
                    'is_primary': True  # Mark this as the primary project being shown
                }],
                'primary_project_id': project['id'],  # Track which project this card represents
                'info_completeness': info_completeness,  # Simple score: more info = easier to evaluate
                'created_at': project.get('created_at')  # Keep for sorting
            }
            
            # Add preference score if calculated
            if preference_score is not None:
                formatted_result['preference_score'] = int(preference_score)
            
            formatted_results.append(formatted_result)
        
        # Sorting strategy
        if user_preferences and any(user_preferences.values()):
            # When preferences are set: Sort by preference score (primary), completeness as tie-breaker
            # We've already fetched more projects (100-200), so we can find the best matches
            formatted_results.sort(
                key=lambda x: (
                    x.get('preference_score', 0),  # Primary: preference match
                    x.get('info_completeness', 0)  # Secondary: more info = easier to evaluate
                ),
                reverse=True
            )
            
            # Soft filtering: Filter out very low matches, but keep enough for good results
            # Since we fetched more projects, we can be more selective
            filtered_results = [
                r for r in formatted_results 
                if r.get('preference_score', 0) >= 20 or r.get('info_completeness', 0) >= 1.5
            ]
            
            # Use filtered results if we have enough, otherwise use all (to avoid empty results)
            if len(filtered_results) >= final_limit:
                formatted_results = filtered_results
            # If filtering leaves too few, keep at least top results regardless of score
            elif len(formatted_results) > final_limit:
                # Keep top results even if some are below threshold
                formatted_results = formatted_results[:max(final_limit, len(filtered_results))]
        else:
            # When no preferences: Sort by info completeness first, then recency
            # This shows projects with more information first (easier to evaluate)
            # rather than just showing newest projects
            formatted_results.sort(
                key=lambda x: (
                    x.get('info_completeness', 0),  # More info = easier to evaluate
                    x.get('created_at', '')  # Then recency
                ),
                reverse=True
            )
        
        # Return top N results (based on final_limit, default 20)
        # Apply pagination offset here if needed
        start_idx = offset
        end_idx = offset + final_limit
        available_founders = formatted_results[start_idx:end_idx]
        # Skip fetching all projects - we already have the single project per card
    else:
        # Legacy founder-based discovery (backward compatible)
        # For legacy mode, we still filter by user-level swipes for backward compatible
        swipes = supabase.table('swipes').select('swiped_id').eq('swiper_id', current_user_id).is_('project_id', None).execute()
        swiped_ids = [swipe['swiped_id'] for swipe in swipes.data] if swipes.data else []
        
        # Get all founders excluding current user
        all_founders = supabase.table('founders').select('*').neq('clerk_user_id', clerk_user_id).execute()
        
        
        # Filter out already swiped founders (only for legacy user-to-user swipes)
        available_founder_ids = [
            founder['id'] for founder in all_founders.data 
            if founder['id'] not in swiped_ids
        ]
        
        # Fetch all projects for available founders in a single query (optimized)
        if available_founder_ids:
            all_projects = supabase.table('projects').select('*').in_('founder_id', available_founder_ids).order('display_order').execute()
            
            # Group projects by founder_id
            projects_by_founder = {}
            if all_projects.data:
                for project in all_projects.data:
                    founder_id = project['founder_id']
                    if founder_id not in projects_by_founder:
                        projects_by_founder[founder_id] = []
                    projects_by_founder[founder_id].append(project)
            
            # Attach projects to founders
            available_founders = []
            for founder in all_founders.data:
                if founder['id'] in available_founder_ids:
                    founder['projects'] = projects_by_founder.get(founder['id'], [])
                    available_founders.append(founder)
        else:
            available_founders = []
    
        # Apply filters if provided (only in legacy mode)
        if filters:
            available_founders = apply_filters_to_founders(available_founders, filters)
    
    return available_founders

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
        # Get all founders and filter by case-insensitive email match
        # Note: Supabase's ilike might not work perfectly, so we'll fetch and filter
        all_founders = supabase.table('founders').select('id, email, clerk_user_id').execute()
        if all_founders.data:
            for founder in all_founders.data:
                founder_email = founder.get('email', '').strip().lower()
                if founder_email == email_lower:
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
    
    # Create founder profile (no credits system - uses plan-based features)
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
    
    # During profile creation, don't charge for projects (part of onboarding)
    # Only charge for projects added AFTER profile is created
    valid_projects = [p for p in projects if p.get('title') and p.get('description') and p.get('stage')]
    
    # Create projects (only if they don't already exist)
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

