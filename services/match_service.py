"""Match-related business logic"""
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from config.database import get_supabase
from services import email_service
from utils.logger import log_error, log_info

# Dissolution cooling-off period in days
DISSOLUTION_COOLOFF_DAYS = 7


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
    """
    Request dissolution of a match/workspace partnership.
    
    IMPORTANT: This no longer instantly deletes data. Instead, it initiates
    the dissolution process with a 7-day cooling-off period.
    
    For immediate dissolution (admin only), use admin_force_unmatch().
    """
    # Redirect to the new safe dissolution flow
    return request_dissolution(clerk_user_id, match_id, reason="User requested unmatch")


def admin_force_unmatch(match_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """
    Admin-only: Force immediate dissolution without cooling-off period.
    
    Use sparingly - this bypasses the normal consent flow.
    Preserves data (archives instead of deletes) but skips notifications.
    """
    supabase = get_supabase()
    
    match = supabase.table('matches').select(
        '*, founder1:founders!founder1_id(id, name, email), founder2:founders!founder2_id(id, name, email)'
    ).eq('id', match_id).execute()
    
    if not match.data:
        raise ValueError("Match not found")
    
    match_data = match.data[0]
    
    # Set dissolution reason
    supabase.table('matches').update({
        'dissolution_reason': reason or 'Admin force dissolution'
    }).eq('id', match_id).execute()
    match_data['dissolution_reason'] = reason or 'Admin force dissolution'
    
    # Execute immediately
    return _execute_dissolution(match_id, match_data, confirmed_by=None)


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


# ============================================
# DISSOLUTION SYSTEM - Safe, consensual partnership ending
# ============================================

def _get_founder_id_from_clerk(clerk_user_id: str) -> str:
    """Helper to get founder ID from clerk user ID."""
    supabase = get_supabase()
    
    try:
        from utils.request_cache import get_cached_founder_id, set_cached_founder_id
        founder_id = get_cached_founder_id(clerk_user_id)
        if founder_id:
            return founder_id
    except ImportError:
        pass
    
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    founder_id = user_profile.data[0]['id']
    
    try:
        from utils.request_cache import set_cached_founder_id
        set_cached_founder_id(clerk_user_id, founder_id)
    except ImportError:
        pass
    
    return founder_id


def _get_match_and_verify_participant(match_id: str, founder_id: str) -> Dict[str, Any]:
    """Get match and verify the founder is a participant."""
    supabase = get_supabase()
    
    match = supabase.table('matches').select(
        '*, founder1:founders!founder1_id(id, name, email), founder2:founders!founder2_id(id, name, email)'
    ).eq('id', match_id).execute()
    
    if not match.data:
        raise ValueError("Match not found")
    
    match_data = match.data[0]
    
    if match_data['founder1_id'] != founder_id and match_data['founder2_id'] != founder_id:
        raise ValueError("You are not part of this match")
    
    return match_data


def _get_other_founder(match_data: Dict, current_founder_id: str) -> Dict[str, Any]:
    """Get the other founder in the match."""
    if match_data['founder1_id'] == current_founder_id:
        return match_data.get('founder2') or {}
    return match_data.get('founder1') or {}


def request_dissolution(
    clerk_user_id: str, 
    match_id: str, 
    reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Request dissolution of a match/workspace partnership.
    
    Starts a 7-day cooling-off period. The other party can:
    - Confirm immediately to complete dissolution
    - Do nothing and dissolution auto-completes after 7 days
    
    The requesting party can cancel during the cooling-off period.
    
    Returns dissolution status and cooloff end date.
    """
    supabase = get_supabase()
    founder_id = _get_founder_id_from_clerk(clerk_user_id)
    match_data = _get_match_and_verify_participant(match_id, founder_id)
    
    # Check if dissolution already in progress
    if match_data.get('dissolution_status') == 'requested':
        cooloff_ends = match_data.get('dissolution_cooloff_ends_at')
        return {
            "status": "already_requested",
            "message": "Dissolution already requested",
            "cooloff_ends_at": cooloff_ends,
            "requested_by": match_data.get('dissolution_requested_by')
        }
    
    if match_data.get('dissolution_status') == 'dissolved':
        raise ValueError("This match has already been dissolved")
    
    now = datetime.now(timezone.utc)
    cooloff_ends = now + timedelta(days=DISSOLUTION_COOLOFF_DAYS)
    
    # Update match with dissolution request
    supabase.table('matches').update({
        'dissolution_status': 'requested',
        'dissolution_requested_at': now.isoformat(),
        'dissolution_requested_by': founder_id,
        'dissolution_reason': (reason or '').strip()[:500] or None,
        'dissolution_cooloff_ends_at': cooloff_ends.isoformat()
    }).eq('id', match_id).execute()
    
    # Also update workspace if exists
    workspace = supabase.table('workspaces').select('id').eq('match_id', match_id).execute()
    if workspace.data:
        workspace_id = workspace.data[0]['id']
        supabase.table('workspaces').update({
            'dissolution_status': 'requested',
            'dissolution_requested_at': now.isoformat(),
            'dissolution_requested_by': founder_id,
            'dissolution_reason': (reason or '').strip()[:500] or None,
            'dissolution_cooloff_ends_at': cooloff_ends.isoformat()
        }).eq('id', workspace_id).execute()
    
    # Notify the other founder
    other_founder = _get_other_founder(match_data, founder_id)
    if other_founder.get('email'):
        try:
            requester = supabase.table('founders').select('name').eq('id', founder_id).execute()
            requester_name = requester.data[0].get('name', 'Your co-founder') if requester.data else 'Your co-founder'
            
            email_service.send_dissolution_request_email(
                to_email=other_founder['email'],
                user_name=other_founder.get('name', 'there'),
                requester_name=requester_name,
                workspace_id=workspace.data[0]['id'] if workspace.data else None,
                cooloff_ends_at=cooloff_ends,
                reason=reason
            )
        except Exception as e:
            log_error(f"Failed to send dissolution request email", error=e)
    
    # Create in-app notification
    try:
        supabase.table('notifications').insert({
            'user_id': other_founder.get('id'),
            'type': 'DISSOLUTION_REQUESTED',
            'title': 'Partnership dissolution requested',
            'message': f"Your co-founder has requested to end the partnership. You have {DISSOLUTION_COOLOFF_DAYS} days to respond.",
            'data': {
                'match_id': match_id,
                'workspace_id': workspace.data[0]['id'] if workspace.data else None,
                'cooloff_ends_at': cooloff_ends.isoformat()
            }
        }).execute()
    except Exception as e:
        log_error(f"Failed to create dissolution notification", error=e)
    
    log_info(f"Dissolution requested for match {match_id} by founder {founder_id}")
    
    return {
        "status": "requested",
        "message": f"Dissolution requested. Your partner has been notified. The partnership will end on {cooloff_ends.strftime('%B %d, %Y')} unless cancelled.",
        "cooloff_ends_at": cooloff_ends.isoformat(),
        "can_cancel": True
    }


def confirm_dissolution(clerk_user_id: str, match_id: str) -> Dict[str, Any]:
    """
    Confirm dissolution of a match/workspace.
    
    Can be called by:
    - The other party (not the requester) to confirm immediately
    - System/cron job after cooling-off period expires
    
    This archives the workspace (read-only) instead of deleting it.
    """
    supabase = get_supabase()
    founder_id = _get_founder_id_from_clerk(clerk_user_id)
    match_data = _get_match_and_verify_participant(match_id, founder_id)
    
    if match_data.get('dissolution_status') != 'requested':
        raise ValueError("No pending dissolution request for this match")
    
    # Check if this is the requester trying to confirm (not allowed - they should cancel instead)
    if match_data.get('dissolution_requested_by') == founder_id:
        raise ValueError("You cannot confirm your own dissolution request. You can cancel it instead.")
    
    # Execute the dissolution
    return _execute_dissolution(match_id, match_data, confirmed_by=founder_id)


def cancel_dissolution_request(clerk_user_id: str, match_id: str) -> Dict[str, Any]:
    """
    Cancel a pending dissolution request.
    
    Only the requester can cancel, and only during the cooling-off period.
    """
    supabase = get_supabase()
    founder_id = _get_founder_id_from_clerk(clerk_user_id)
    match_data = _get_match_and_verify_participant(match_id, founder_id)
    
    if match_data.get('dissolution_status') != 'requested':
        raise ValueError("No pending dissolution request to cancel")
    
    if match_data.get('dissolution_requested_by') != founder_id:
        raise ValueError("Only the person who requested dissolution can cancel it")
    
    # Clear dissolution request on match
    supabase.table('matches').update({
        'dissolution_status': 'active',
        'dissolution_requested_at': None,
        'dissolution_requested_by': None,
        'dissolution_reason': None,
        'dissolution_cooloff_ends_at': None
    }).eq('id', match_id).execute()
    
    # Clear on workspace too
    workspace = supabase.table('workspaces').select('id').eq('match_id', match_id).execute()
    if workspace.data:
        supabase.table('workspaces').update({
            'dissolution_status': 'active',
            'dissolution_requested_at': None,
            'dissolution_requested_by': None,
            'dissolution_reason': None,
            'dissolution_cooloff_ends_at': None
        }).eq('id', workspace.data[0]['id']).execute()
    
    # Notify the other founder
    other_founder = _get_other_founder(match_data, founder_id)
    if other_founder.get('id'):
        try:
            supabase.table('notifications').insert({
                'user_id': other_founder.get('id'),
                'type': 'DISSOLUTION_CANCELLED',
                'title': 'Dissolution request cancelled',
                'message': 'Your co-founder has cancelled the dissolution request. Your partnership continues.',
                'data': {'match_id': match_id}
            }).execute()
        except Exception as e:
            log_error(f"Failed to create cancellation notification", error=e)
    
    log_info(f"Dissolution cancelled for match {match_id} by founder {founder_id}")
    
    return {
        "status": "cancelled",
        "message": "Dissolution request cancelled. Your partnership continues."
    }


def get_dissolution_status(clerk_user_id: str, match_id: str) -> Dict[str, Any]:
    """Get the current dissolution status for a match."""
    founder_id = _get_founder_id_from_clerk(clerk_user_id)
    match_data = _get_match_and_verify_participant(match_id, founder_id)
    
    status = match_data.get('dissolution_status', 'active')
    
    result = {
        "status": status,
        "requested_at": match_data.get('dissolution_requested_at'),
        "requested_by": match_data.get('dissolution_requested_by'),
        "reason": match_data.get('dissolution_reason'),
        "cooloff_ends_at": match_data.get('dissolution_cooloff_ends_at'),
    }
    
    if status == 'requested':
        is_requester = match_data.get('dissolution_requested_by') == founder_id
        result['is_requester'] = is_requester
        result['can_cancel'] = is_requester
        result['can_confirm'] = not is_requester
        
        # Check if cooloff has expired
        cooloff_ends = match_data.get('dissolution_cooloff_ends_at')
        if cooloff_ends:
            try:
                if isinstance(cooloff_ends, str):
                    from dateutil import parser
                    cooloff_ends = parser.parse(cooloff_ends)
                if datetime.now(timezone.utc) >= cooloff_ends.replace(tzinfo=timezone.utc):
                    result['cooloff_expired'] = True
            except Exception:
                pass
    
    return result


def _execute_dissolution(
    match_id: str, 
    match_data: Dict[str, Any], 
    confirmed_by: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute the actual dissolution - archive workspace, update statuses.
    
    Called by confirm_dissolution or by auto-expiry cron.
    """
    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    
    project_id = match_data.get('project_id')
    founder1_id = match_data['founder1_id']
    founder2_id = match_data['founder2_id']
    
    # Archive workspace (NOT delete)
    workspace = supabase.table('workspaces').select('id').eq('match_id', match_id).execute()
    workspace_id = None
    
    if workspace.data:
        workspace_id = workspace.data[0]['id']
        
        # Archive the workspace - it becomes read-only but data is preserved
        supabase.table('workspaces').update({
            'is_archived': True,
            'archived_at': now.isoformat(),
            'archived_by': confirmed_by or match_data.get('dissolution_requested_by'),
            'dissolution_status': 'dissolved',
            'dissolution_confirmed_by': confirmed_by
        }).eq('id', workspace_id).execute()
        
        # Free up advisor slots (they're no longer active in this workspace)
        try:
            advisors = supabase.table('workspace_participants').select('user_id').eq(
                'workspace_id', workspace_id
            ).eq('role', 'ADVISOR').execute()
            
            if advisors.data:
                for advisor in advisors.data:
                    try:
                        _free_advisor_slot(supabase, advisor['user_id'])
                    except Exception as e:
                        log_error(f"Failed to free advisor slot", error=e)
        except Exception as e:
            log_error(f"Failed to get advisors for workspace {workspace_id}", error=e)
    
    # Update match status (NOT delete)
    supabase.table('matches').update({
        'dissolution_status': 'dissolved',
        'dissolution_confirmed_by': confirmed_by,
        'is_expired': True  # Mark as expired so it doesn't show in active matches
    }).eq('id', match_id).execute()
    
    # Update application status to withdrawn
    if project_id:
        try:
            # Find the accepted application for this project
            supabase.table('applications').update({
                'status': 'withdrawn',
                'rejection_reason': 'Partnership dissolved'
            }).eq('project_id', project_id).eq('status', 'accepted').in_(
                'applicant_id', [founder1_id, founder2_id]
            ).execute()
            
            # Restore project to seeking_cofounder
            supabase.table('projects').update({
                'seeking_cofounder': True
            }).eq('id', project_id).execute()
        except Exception as e:
            log_error(f"Failed to update application/project status", error=e)
    
    # Notify both founders
    for founder_id in [founder1_id, founder2_id]:
        try:
            supabase.table('notifications').insert({
                'user_id': founder_id,
                'type': 'DISSOLUTION_COMPLETE',
                'title': 'Partnership ended',
                'message': 'Your partnership has been dissolved. Your workspace is now archived and read-only.',
                'data': {
                    'match_id': match_id,
                    'workspace_id': workspace_id,
                    'archived': True
                }
            }).execute()
        except Exception as e:
            log_error(f"Failed to create dissolution complete notification", error=e)
    
    log_info(f"Dissolution completed for match {match_id}")
    
    return {
        "status": "dissolved",
        "message": "Partnership has been dissolved. Your workspace is now archived and accessible in read-only mode.",
        "workspace_id": workspace_id,
        "archived": True
    }


def process_expired_dissolution_requests() -> Dict[str, Any]:
    """
    Cron job to auto-complete dissolution requests after cooling-off expires.
    
    Should be called periodically (e.g., daily).
    """
    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    
    # Find matches with expired cooling-off periods
    expired = supabase.table('matches').select(
        '*, founder1:founders!founder1_id(id, name, email), founder2:founders!founder2_id(id, name, email)'
    ).eq('dissolution_status', 'requested').lt(
        'dissolution_cooloff_ends_at', now.isoformat()
    ).execute()
    
    results = {
        'processed': 0,
        'errors': []
    }
    
    for match_data in (expired.data or []):
        try:
            _execute_dissolution(match_data['id'], match_data, confirmed_by=None)
            results['processed'] += 1
            log_info(f"Auto-completed dissolution for match {match_data['id']}")
        except Exception as e:
            error_msg = f"Failed to auto-complete dissolution for match {match_data['id']}: {str(e)}"
            results['errors'].append(error_msg)
            log_error(error_msg)
    
    return results
