"""Advanced search service for Pro+ users - project-based search with scoring"""
import json
from typing import Dict, List, Optional, Any
from config.database import get_supabase
from services.plan_service import get_founder_plan


def check_pro_plus_access(clerk_user_id: str) -> bool:
    """Verify user has PRO_PLUS plan"""
    try:
        plan_config = get_founder_plan(clerk_user_id)
        return plan_config.get('id') == 'PRO_PLUS'
    except Exception:
        return False


def search_projects(query_params: Dict[str, Any], current_user_id: str) -> Dict[str, Any]:
    """
    Search projects with advanced filters and scoring.
    
    Uses hybrid approach:
    1. Fetches larger initial set (200-300) from database with filters
    2. Applies keyword filtering at DB level using ILIKE
    3. Scores and filters in Python
    4. Returns top N results with proper pagination
    
    Args:
        query_params: Dictionary with search parameters:
            - q: keyword search (optional)
            - genre: list of genres (optional)
            - stage: list of stages (optional)
            - region: region filter (optional)
            - timezone_offset_range: timezone range like "-3..+3" (optional)
            - limit: number of results to return (default: 50)
            - offset: pagination offset (default: 0)
        current_user_id: UUID of current user (to exclude their own projects)
    
    Returns:
        Dictionary with:
            - projects: List of projects with scores, sorted by score descending
            - total: Total number of matching projects (after filtering)
    """
    supabase = get_supabase()
    
    # Extract query parameters
    keyword_raw = query_params.get('q', '').strip() if query_params.get('q') else ''
    keyword = keyword_raw.lower() if keyword_raw else None
    genres = query_params.get('genre', [])
    if isinstance(genres, str):
        genres = [genres]
    stages = query_params.get('stage', [])
    if isinstance(stages, str):
        stages = [stages]
    region = query_params.get('region', '').strip().lower() if query_params.get('region') else None
    timezone_range = query_params.get('timezone_offset_range')
    
    # Pagination parameters for final results
    result_limit = query_params.get('limit', 50)  # Number of results to return
    result_offset = query_params.get('offset', 0)  # Pagination offset
    
    # Fetch larger initial set to account for filtering
    # Multiplier of 4-6x ensures we have enough candidates after filtering
    initial_fetch_limit = max(200, result_limit * 4)  # At least 200, or 4x the requested limit
    
    # Build base query - get projects with founder info
    query = supabase.table('projects').select(
        '*, founder:founders!founder_id(*)'
    ).neq('founder_id', current_user_id).eq('is_active', True).eq('seeking_cofounder', True)
    
    # Apply database-level filters (for efficiency)
    if stages:
        query = query.in_('stage', stages)
    
    if genres:
        query = query.in_('genre', genres)
    
    # Apply keyword filtering at database level using ILIKE
    # This reduces the number of projects we need to process in Python
    if keyword:
        # Use OR condition to search in both title and description
        # Supabase doesn't support OR directly, so we'll filter in Python for now
        # But we can still use ILIKE for initial filtering
        # Note: We'll do keyword filtering in Python after fetching for better control
        pass  # Keyword filtering done in Python for now (can be optimized later with full-text search)
    
    # Order by most recent first (we'll re-sort by score later)
    query = query.order('created_at', desc=True)
    
    # Fetch larger initial set (no offset here - we want the most recent candidates)
    query = query.range(0, initial_fetch_limit - 1)
    
    all_projects = query.execute()
    
    if not all_projects.data:
        return {
            'projects': [],
            'total': 0
        }
    
    # Get project IDs for filtering out already swiped/workspaced projects
    project_ids = [p['id'] for p in all_projects.data]
    
    # Get projects that already have workspaces (exclude them) - optimized after migration
    # One project per workspace (one project, two founders)
    workspace_project_ids = set()
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
                # Get workspaces for these matches
                if match_ids:
                    workspaces = supabase.table('workspaces').select('match_id').in_('match_id', match_ids).execute()
                    if workspaces.data:
                        # Map match_ids back to project_ids
                        match_to_project = {m['id']: m['project_id'] for m in matches.data if m.get('project_id')}
                        for wp in workspaces.data:
                            match_id = wp.get('match_id')
                            if match_id and match_id in match_to_project:
                                workspace_project_ids.add(match_to_project[match_id])
    
    # Get projects already swiped by current user
    swiped_combinations = set()
    if project_ids:
        user_swipes = supabase.table('swipes').select('project_id, swiped_id').eq('swiper_id', current_user_id).in_('project_id', project_ids).execute()
        if user_swipes.data:
            for swipe in user_swipes.data:
                if swipe.get('project_id'):
                    swiped_combinations.add((swipe['project_id'], swipe['swiped_id']))
    
    # Filter and score projects
    scored_projects = []
    for project in all_projects.data:
        project_id = project['id']
        project_owner_id = project['founder_id']
        founder_info = project.get('founder', {})
        
        # Skip if project has workspace or already swiped
        if (project_id in workspace_project_ids or 
            (project_id, project_owner_id) in swiped_combinations):
            continue
        
        # Apply keyword filtering at Python level (for now)
        # If keyword is provided, check if it matches title or description
        # Note: keyword is already lowercased from query_params processing
        if keyword:
            project_title = (project.get('title') or '').lower()
            project_description = (project.get('description') or '').lower()
            founder_name = (founder_info.get('name') or '').lower()
            
            # Skip if keyword doesn't match anywhere
            if (keyword not in project_title and 
                keyword not in project_description and
                keyword not in founder_name):
                continue
        
        # Calculate score for this project
        score = _calculate_project_score(project, keyword, genres, stages, region)
        
        # Apply timezone filter if specified
        if timezone_range and not _matches_timezone_range(founder_info, timezone_range):
            continue
        
        # Only include projects with score > 0 (at least one match)
        if score > 0:
            # Format project result
            formatted_project = {
                'id': project['id'],
                'name': project.get('title', 'Untitled Project'),
                'description_snippet': _truncate_description(project.get('description', '')),
                'description': project.get('description', ''),
                'stage': project.get('stage', 'idea'),
                'genre': project.get('genre'),
                'region': founder_info.get('location', ''),
                'founder': {
                    'id': founder_info.get('id'),
                    'name': founder_info.get('name', ''),
                    'headline': founder_info.get('looking_for', ''),
                    'location': founder_info.get('location', ''),
                    'skills': founder_info.get('skills', []),
                    'profile_picture_url': founder_info.get('profile_picture_url'),
                },
                'score': round(score, 2),
                'created_at': project.get('created_at'),
                'needed_skills': project.get('needed_skills', []),
                'compatibility_answers': project.get('compatibility_answers', {})
            }
            
            scored_projects.append(formatted_project)
    
    # Sort by score descending
    scored_projects.sort(key=lambda x: x['score'], reverse=True)
    
    # Store total count before pagination
    total_count = len(scored_projects)
    
    # Apply pagination to final results
    paginated_projects = scored_projects[result_offset:result_offset + result_limit]
    
    return {
        'projects': paginated_projects,
        'total': total_count
    }


def _calculate_project_score(project: Dict[str, Any], keyword: Optional[str], 
                             genres: List[str], stages: List[str], 
                             region: Optional[str]) -> float:
    """
    Calculate relevance score for a project (0-1 scale).
    
    Scoring weights:
    - Keyword match in title: 0.4
    - Keyword match in description: 0.2
    - Genre match: 0.2
    - Stage match: 0.1
    - Region match: 0.1
    """
    score = 0.0
    
    project_title = (project.get('title') or '').lower()
    project_description = (project.get('description') or '').lower()
    project_genre = (project.get('genre') or '').lower()
    project_stage = project.get('stage', '')
    founder_info = project.get('founder', {})
    founder_location = (founder_info.get('location') or '').lower()
    founder_name = (founder_info.get('name') or '').lower()
    
    # Keyword match in title (0.4)
    if keyword:
        if keyword in project_title:
            score += 0.4
        # Also check founder name for keyword match
        elif keyword in founder_name:
            score += 0.3  # Slightly less weight for founder name match
    
    # Keyword match in description (0.2)
    if keyword and keyword in project_description:
        score += 0.2
    
    # Genre match (0.2) - single genre match
    if genres:
        if any(g.lower() == project_genre for g in genres):
            score += 0.2
    
    # Stage match (0.1)
    if stages:
        if project_stage in stages:
            score += 0.1
    
    # Region match (0.1) - from founder's location
    if region:
        if region in founder_location:
            score += 0.1
    
    return min(score, 1.0)


def _truncate_description(description: str, max_length: int = 200) -> str:
    """Truncate description to max_length with ellipsis"""
    if len(description) <= max_length:
        return description
    return description[:max_length].rsplit(' ', 1)[0] + '...'


def _matches_timezone_range(founder_info: Dict[str, Any], timezone_range: str) -> bool:
    """
    Check if founder's timezone is within the specified range.
    Format: "-3..+3" means -3 to +3 hours from user's timezone.
    
    Note: This is a simplified implementation. For full timezone support,
    we'd need to store timezone in founders table and do proper timezone math.
    For now, if timezone_range is provided but founder doesn't have timezone,
    we'll return True (don't filter out).
    """
    # TODO: Implement proper timezone matching when timezone column is added to founders
    # For now, if timezone_range is provided, we'll skip this filter
    # (or implement basic matching if timezone data exists)
    founder_timezone = founder_info.get('timezone')
    if not founder_timezone:
        return True  # Don't filter out if no timezone data
    
    # Parse range (e.g., "-3..+3")
    try:
        if '..' in timezone_range:
            min_offset, max_offset = timezone_range.split('..')
            min_offset = int(min_offset.replace('+', ''))
            max_offset = int(max_offset.replace('+', ''))
            # Basic implementation - would need proper timezone offset extraction
            # For now, return True to not filter out
            return True
    except Exception:
        pass
    
    return True  # Default: don't filter out

