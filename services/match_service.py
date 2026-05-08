"""Match-related business logic"""
from datetime import datetime, timezone, timedelta
from config.database import get_supabase
from services import email_service


def get_matches(clerk_user_id):
    """Get matches for the current user (excludes expired matches)"""
    supabase = get_supabase()
    
    # Get current user's founder ID
    try:
        from utils.request_cache import get_cached_founder_id, set_cached_founder_id
        current_user_id = get_cached_founder_id(clerk_user_id)
    except ImportError:
        current_user_id = None
    
    if not current_user_id:
        user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not user_profile.data:
            raise ValueError("Profile not found")
        current_user_id = user_profile.data[0]['id']
        try:
            from utils.request_cache import set_cached_founder_id
            set_cached_founder_id(clerk_user_id, current_user_id)
        except ImportError:
            pass
    
    # Check and mark expired matches
    _check_and_mark_expired_matches()
    
    # Get matches where current user is founder1 or founder2 (exclude expired)
    try:
        all_matches_result = supabase.table('matches').select(
            '*, founder1:founders!founder1_id(*), founder2:founders!founder2_id(*)'
        ).or_(f'founder1_id.eq.{current_user_id},founder2_id.eq.{current_user_id}').eq('is_expired', False).execute()
        all_matches = all_matches_result.data if all_matches_result.data else []
    except (AttributeError, Exception):
        # Fallback to two queries if OR syntax not supported
        matches1 = supabase.table('matches').select('*, founder1:founders!founder1_id(*), founder2:founders!founder2_id(*)').eq('founder1_id', current_user_id).eq('is_expired', False).execute()
        matches2 = supabase.table('matches').select('*, founder1:founders!founder1_id(*), founder2:founders!founder2_id(*)').eq('founder2_id', current_user_id).eq('is_expired', False).execute()
        all_matches = []
        if matches1.data:
            all_matches.extend(matches1.data)
        if matches2.data:
            all_matches.extend(matches2.data)
    
    # Batch fetch all projects
    founder_ids = set()
    project_ids = set()
    for match in all_matches:
        if match.get('founder1_id'):
            founder_ids.add(match['founder1_id'])
        if match.get('founder2_id'):
            founder_ids.add(match['founder2_id'])
        if match.get('project_id'):
            project_ids.add(match['project_id'])
    
    # Batch fetch founder projects
    founder_projects_map = {}
    if founder_ids:
        all_founder_projects = supabase.table('projects').select('*').in_('founder_id', list(founder_ids)).order('display_order').execute()
        if all_founder_projects.data:
            for project in all_founder_projects.data:
                founder_id = project['founder_id']
                if founder_id not in founder_projects_map:
                    founder_projects_map[founder_id] = []
                founder_projects_map[founder_id].append(project)
    
    # Batch fetch match projects
    match_projects_map = {}
    if project_ids:
        all_match_projects = supabase.table('projects').select('*').in_('id', list(project_ids)).execute()
        if all_match_projects.data:
            for project in all_match_projects.data:
                match_projects_map[project['id']] = project
    
    # Format matches
    formatted_matches = []
    for match in all_matches:
        match_project_id = match.get('project_id')
        
        # Skip legacy matches without project_id
        if not match_project_id:
            continue
        
        if match['founder1_id'] == current_user_id:
            other_founder = match.get('founder2') or {}
        else:
            other_founder = match.get('founder1') or {}
        
        if other_founder.get('id'):
            founder_id = other_founder['id']
            other_founder['projects'] = founder_projects_map.get(founder_id, [])
        
        match_project = match_projects_map.get(match_project_id)
        
        # Get compatibility score
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
            'project': match_project,
            'is_project_based': True,
            'compatibility_score': compatibility_score
        })
    
    return formatted_matches


def _check_and_mark_expired_matches():
    """Check and mark expired matches (30 days with no workspace activity)"""
    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    
    # Get matches that are expired but not marked
    expired_matches = supabase.table('matches').select('id, expires_at').eq('is_expired', False).lt('expires_at', now.isoformat()).execute()
    
    if expired_matches.data:
        match_ids = [m['id'] for m in expired_matches.data]
        
        for match_id in match_ids:
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
                                continue
                    except Exception:
                        pass
            
            supabase.table('matches').update({'is_expired': True}).eq('id', match_id).execute()


def unmatch(clerk_user_id, match_id):
    """Remove a match (unmatch) and dissolve the partnership"""
    from utils.logger import log_error, log_info
    
    supabase = get_supabase()
    
    # Get current user's founder ID
    try:
        from utils.request_cache import get_cached_founder_id, set_cached_founder_id
        current_user_id = get_cached_founder_id(clerk_user_id)
    except ImportError:
        current_user_id = None
    
    if not current_user_id:
        user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not user_profile.data:
            raise ValueError("Profile not found")
        current_user_id = user_profile.data[0]['id']
        try:
            from utils.request_cache import set_cached_founder_id
            set_cached_founder_id(clerk_user_id, current_user_id)
        except ImportError:
            pass
    
    # Verify user is part of this match
    match = supabase.table('matches').select('*').eq('id', match_id).execute()
    if not match.data:
        raise ValueError("Match not found")
    
    match_data = match.data[0]
    if match_data['founder1_id'] != current_user_id and match_data['founder2_id'] != current_user_id:
        raise ValueError("You are not part of this match")
    
    project_id = match_data.get('project_id')
    
    # Delete workspace first
    try:
        workspace = supabase.table('workspaces').select('id').eq('match_id', match_id).execute()
        if workspace.data:
            workspace_id = workspace.data[0]['id']
            
            # Find advisors before deleting
            advisor_user_ids = []
            try:
                advisors = supabase.table('workspace_participants').select('user_id').eq(
                    'workspace_id', workspace_id
                ).eq('role', 'ADVISOR').execute()
                if advisors.data:
                    advisor_user_ids = [a['user_id'] for a in advisors.data]
                    log_info(f"Found {len(advisor_user_ids)} advisor(s) to free up")
            except Exception as e:
                log_error(f"Failed to get advisors for workspace {workspace_id}", error=e)
            
            # Delete participants
            supabase.table('workspace_participants').delete().eq('workspace_id', workspace_id).execute()
            
            # Free advisor slots
            for advisor_user_id in advisor_user_ids:
                try:
                    _free_advisor_slot(supabase, advisor_user_id)
                except Exception as e:
                    log_error(f"Failed to free advisor slot for {advisor_user_id}", error=e)
            
            # Delete workspace
            supabase.table('workspaces').delete().eq('id', workspace_id).execute()
    except Exception as e:
        log_error(f"Failed to delete workspace after unmatch {match_id}", error=e)
    
    # Delete the match
    supabase.table('matches').delete().eq('id', match_id).execute()
    
    # Restore project state
    if project_id:
        try:
            supabase.table('projects').update({'seeking_cofounder': True}).eq('id', project_id).execute()
        except Exception as e:
            log_error(f"Failed to restore project state after unmatch {match_id}", error=e)
    
    return {"message": "Match removed successfully"}


def _free_advisor_slot(supabase, advisor_user_id):
    """Free up an advisor slot when they're removed from a workspace."""
    advisor_profile = supabase.table('advisor_profiles').select(
        'id, max_active_workspaces'
    ).eq('user_id', advisor_user_id).execute()
    
    if not advisor_profile.data:
        return
    
    active_count_result = supabase.table('workspace_participants').select('workspace_id').eq(
        'user_id', advisor_user_id
    ).eq('role', 'ADVISOR').execute()
    
    current_active = len(active_count_result.data) if active_count_result.data else 0
    max_active = advisor_profile.data[0].get('max_active_workspaces', 0)
    
    update_data = {'current_active_workspaces': current_active}
    
    if current_active < max_active:
        update_data['is_discoverable'] = True
    
    supabase.table('advisor_profiles').update(update_data).eq('user_id', advisor_user_id).execute()
