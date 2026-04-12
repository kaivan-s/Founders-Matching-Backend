"""Project Access Service - Handles visibility and access requests for projects"""
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from config.database import get_supabase
from utils.logger import log_info, log_error
from services import email_service

# Visibility options
VISIBILITY_OPEN = 'open'
VISIBILITY_REQUEST_ACCESS = 'request_access'

VALID_VISIBILITY_OPTIONS = [VISIBILITY_OPEN, VISIBILITY_REQUEST_ACCESS]


def _get_founder_id(clerk_user_id: str) -> str:
    """Get founder ID from Clerk user ID"""
    supabase = get_supabase()
    result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not result.data:
        raise ValueError("Founder not found")
    return result.data[0]['id']


def update_project_visibility(clerk_user_id: str, project_id: str, visibility: str, 
                             auto_approve_verified: bool = False, 
                             request_expires_days: int = 7) -> Dict[str, Any]:
    """
    Update project visibility settings
    
    Args:
        clerk_user_id: Owner's Clerk user ID
        project_id: Project ID
        visibility: 'open', 'request_access'
        request_expires_days: Days until pending requests expire
    
    Returns:
        Updated project data
    """
    if visibility not in VALID_VISIBILITY_OPTIONS:
        raise ValueError(f"Invalid visibility. Must be one of: {VALID_VISIBILITY_OPTIONS}")
    
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Verify ownership
    project = supabase.table('projects').select('id, founder_id').eq('id', project_id).execute()
    if not project.data:
        raise ValueError("Project not found")
    if project.data[0]['founder_id'] != founder_id:
        raise ValueError("Not authorized to modify this project")
    
    # Update visibility
    result = supabase.table('projects').update({
        'visibility': visibility,
        'auto_approve_verified': auto_approve_verified,
        'request_expires_days': request_expires_days
    }).eq('id', project_id).execute()
    
    log_info(f"Updated project {project_id} visibility to {visibility}")
    
    return result.data[0] if result.data else {}


def get_project_visibility(project_id: str) -> Dict[str, Any]:
    """Get project visibility settings"""
    supabase = get_supabase()
    result = supabase.table('projects').select(
        'visibility, auto_approve_verified, request_expires_days'
    ).eq('id', project_id).execute()
    
    if not result.data:
        raise ValueError("Project not found")
    
    return result.data[0]


def check_user_access(clerk_user_id: str, project_id: str) -> Dict[str, Any]:
    """
    Check if user has access to view full project details
    
    Returns:
        {
            'has_access': bool,
            'reason': str,  # 'owner', 'open', 'verified', 'granted', 'matched', 'pending_request', 'no_access'
            'request_status': str or None  # If there's a pending/declined request
        }
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get project with visibility and owner info
    project = supabase.table('projects').select(
        'id, founder_id, visibility, auto_approve_verified'
    ).eq('id', project_id).execute()
    
    if not project.data:
        raise ValueError("Project not found")
    
    project_data = project.data[0]
    owner_id = project_data['founder_id']
    visibility = project_data.get('visibility', 'open')
    
    # Owner always has access
    if founder_id == owner_id:
        return {'has_access': True, 'reason': 'owner', 'request_status': None}
    
    # Open visibility = everyone has access
    if visibility == VISIBILITY_OPEN:
        return {'has_access': True, 'reason': 'open', 'request_status': None}
    
    # Request access = check for existing grant or pending request
    if visibility == VISIBILITY_REQUEST_ACCESS:
        # Check if access was granted
        grant = supabase.table('project_access_grants').select('id').eq(
            'project_id', project_id
        ).eq('user_id', founder_id).execute()
        
        if grant.data:
            return {'has_access': True, 'reason': 'granted', 'request_status': None}
        
        # Check for existing request
        request = supabase.table('project_access_requests').select(
            'status'
        ).eq('project_id', project_id).eq('requester_id', founder_id).execute()
        
        if request.data:
            status = request.data[0]['status']
            return {'has_access': False, 'reason': 'pending_request' if status == 'pending' else 'no_access', 'request_status': status}
        
        return {'has_access': False, 'reason': 'no_access', 'request_status': None}
    
    # Default: no access
    return {'has_access': False, 'reason': 'no_access', 'request_status': None}


def request_project_access(clerk_user_id: str, project_id: str, message: str = None) -> Dict[str, Any]:
    """
    Request access to view a locked project
    
    Args:
        clerk_user_id: Requester's Clerk user ID
        project_id: Project to request access to
        message: Optional message explaining interest
    
    Returns:
        Request data or auto-approved grant
    """
    requester_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get project info
    project = supabase.table('projects').select(
        'id, founder_id, visibility, auto_approve_verified, request_expires_days, title'
    ).eq('id', project_id).execute()
    
    if not project.data:
        raise ValueError("Project not found")
    
    project_data = project.data[0]
    owner_id = project_data['founder_id']
    visibility = project_data.get('visibility', 'open')
    
    # Can't request access to own project
    if requester_id == owner_id:
        raise ValueError("Cannot request access to your own project")
    
    # Only request_access visibility requires requests
    if visibility != VISIBILITY_REQUEST_ACCESS:
        raise ValueError(f"This project doesn't require access requests (visibility: {visibility})")
    
    # Check for existing request
    existing = supabase.table('project_access_requests').select('id, status').eq(
        'project_id', project_id
    ).eq('requester_id', requester_id).execute()
    
    if existing.data:
        status = existing.data[0]['status']
        if status == 'pending':
            raise ValueError("You already have a pending request for this project")
        elif status == 'approved':
            raise ValueError("You already have access to this project")
        elif status == 'declined':
            # Allow re-requesting after decline (update existing record)
            pass
    
    # Check access request limit (only for new requests, not re-requests after decline)
    if not existing.data:
        from services import plan_service
        can_request, current_count, max_allowed = plan_service.check_access_request_limit(clerk_user_id)
        if not can_request:
            raise ValueError(f"Access request limit reached ({current_count}/{max_allowed} this month). Upgrade to Pro for unlimited requests.")
    
    # Calculate expiry
    expires_days = project_data.get('request_expires_days', 7)
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
    
    # Create or update request
    request_data = {
        'project_id': project_id,
        'requester_id': requester_id,
        'owner_id': owner_id,
        'message': message,
        'status': 'pending',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'expires_at': expires_at.isoformat(),
        'responded_at': None
    }
    
    if existing.data:
        # Update existing declined request
        result = supabase.table('project_access_requests').update(request_data).eq(
            'id', existing.data[0]['id']
        ).execute()
    else:
        # Create new request
        result = supabase.table('project_access_requests').insert(request_data).execute()
    
    log_info(f"Access request created from {requester_id} for project {project_id}")
    
    # Send email notification to project owner
    try:
        requester = supabase.table('founders').select('name').eq('id', requester_id).execute()
        owner = supabase.table('founders').select('name, email').eq('id', owner_id).execute()
        
        if requester.data and owner.data:
            email_service.send_access_request_email(
                to_email=owner.data[0].get('email'),
                user_name=owner.data[0].get('name', 'there'),
                requester_name=requester.data[0].get('name', 'Someone'),
                project_name=project_data.get('title', 'your project'),
                request_message=message
            )
    except Exception as e:
        log_error(f"Failed to send access request notification email", error=e)
    
    return {
        'status': 'pending',
        'request_id': result.data[0]['id'] if result.data else None,
        'expires_at': expires_at.isoformat(),
        'message': 'Access request sent to project owner'
    }


def grant_project_access(clerk_user_id_or_owner_id: str, project_id: str, 
                         user_id_to_grant: str, auto_approved: bool = False) -> Dict[str, Any]:
    """
    Grant access to a user for a project (internal function)
    
    Args:
        clerk_user_id_or_owner_id: Owner's Clerk user ID or founder ID
        project_id: Project ID
        user_id_to_grant: Founder ID to grant access to
        auto_approved: Whether this was auto-approved
    """
    supabase = get_supabase()
    
    # Determine if we have clerk_user_id or founder_id
    try:
        owner_id = _get_founder_id(clerk_user_id_or_owner_id)
    except ValueError:
        # Assume it's already a founder_id
        owner_id = clerk_user_id_or_owner_id
    
    # Create grant
    grant_data = {
        'project_id': project_id,
        'user_id': user_id_to_grant,
        'granted_by': owner_id,
        'granted_at': datetime.now(timezone.utc).isoformat()
    }
    
    # Use upsert to handle existing grants
    result = supabase.table('project_access_grants').upsert(
        grant_data, on_conflict='project_id,user_id'
    ).execute()
    
    return result.data[0] if result.data else {}


def respond_to_access_request(clerk_user_id: str, request_id: str, 
                              action: str) -> Dict[str, Any]:
    """
    Approve or decline an access request
    
    Args:
        clerk_user_id: Project owner's Clerk user ID
        request_id: Access request ID
        action: 'approve' or 'decline'
    
    Returns:
        Updated request data
    """
    if action not in ['approve', 'decline']:
        raise ValueError("Action must be 'approve' or 'decline'")
    
    owner_founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get request
    request = supabase.table('project_access_requests').select(
        'id, project_id, requester_id, owner_id, status'
    ).eq('id', request_id).execute()
    
    if not request.data:
        raise ValueError("Access request not found")
    
    request_data = request.data[0]
    
    # Verify ownership
    if request_data['owner_id'] != owner_founder_id:
        raise ValueError("Not authorized to respond to this request")
    
    # Check if already responded
    if request_data['status'] != 'pending':
        raise ValueError(f"Request already {request_data['status']}")
    
    # Update request status
    new_status = 'approved' if action == 'approve' else 'declined'
    supabase.table('project_access_requests').update({
        'status': new_status,
        'responded_at': datetime.now(timezone.utc).isoformat()
    }).eq('id', request_id).execute()
    
    # If approved, create access grant
    if action == 'approve':
        grant_project_access(
            owner_founder_id, 
            request_data['project_id'], 
            request_data['requester_id']
        )
    
    log_info(f"Access request {request_id} {new_status} by {clerk_user_id}")
    
    # Send email notification to requester if approved
    if action == 'approve':
        try:
            requester = supabase.table('founders').select('name, email').eq('id', request_data['requester_id']).execute()
            owner = supabase.table('founders').select('name').eq('id', owner_founder_id).execute()
            project = supabase.table('projects').select('title').eq('id', request_data['project_id']).execute()
            
            if requester.data and owner.data and project.data:
                email_service.send_access_granted_email(
                    to_email=requester.data[0].get('email'),
                    user_name=requester.data[0].get('name', 'there'),
                    project_name=project.data[0].get('title', 'the project'),
                    owner_name=owner.data[0].get('name', 'The project owner')
                )
        except Exception as e:
            log_error(f"Failed to send access granted notification email", error=e)
    
    return {
        'request_id': request_id,
        'status': new_status,
        'message': f'Access request {new_status}'
    }


def get_pending_requests_for_owner(clerk_user_id: str) -> List[Dict[str, Any]]:
    """
    Get all pending access requests for projects owned by this user
    
    Returns list of requests with requester info
    """
    owner_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get pending requests with requester info and project info
    requests = supabase.table('project_access_requests').select(
        '''
        id,
        project_id,
        message,
        status,
        created_at,
        expires_at,
        projects!inner(id, title),
        founders!project_access_requests_requester_id_fkey(
            id, name, email, location, skills, linkedin_url, linkedin_verified, profile_picture_url
        )
        '''
    ).eq('owner_id', owner_id).eq('status', 'pending').order('created_at', desc=True).execute()
    
    # Format response
    result = []
    for req in requests.data or []:
        requester = req.get('founders', {})
        project = req.get('projects', {})
        result.append({
            'id': req['id'],
            'project_id': req['project_id'],
            'project_title': project.get('title', 'Unknown Project'),
            'message': req.get('message'),
            'created_at': req['created_at'],
            'expires_at': req.get('expires_at'),
            'requester': {
                'id': requester.get('id'),
                'name': requester.get('name'),
                'email': requester.get('email'),
                'location': requester.get('location'),
                'skills': requester.get('skills', []),
                'linkedin_url': requester.get('linkedin_url'),
                'linkedin_verified': requester.get('linkedin_verified', False),
                'profile_picture_url': requester.get('profile_picture_url')
            }
        })
    
    return result


def get_my_access_requests(clerk_user_id: str) -> List[Dict[str, Any]]:
    """
    Get access requests made by this user
    """
    requester_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    requests = supabase.table('project_access_requests').select(
        '''
        id,
        project_id,
        message,
        status,
        created_at,
        responded_at,
        expires_at,
        projects!inner(id, title, founder_id),
        founders!project_access_requests_owner_id_fkey(id, name)
        '''
    ).eq('requester_id', requester_id).order('created_at', desc=True).execute()
    
    result = []
    for req in requests.data or []:
        project = req.get('projects', {})
        owner = req.get('founders', {})
        result.append({
            'id': req['id'],
            'project_id': req['project_id'],
            'project_title': project.get('title', 'Unknown'),
            'owner_name': owner.get('name', 'Unknown'),
            'message': req.get('message'),
            'status': req['status'],
            'created_at': req['created_at'],
            'responded_at': req.get('responded_at'),
            'expires_at': req.get('expires_at')
        })
    
    return result


def get_project_viewers(clerk_user_id: str, project_id: str) -> List[Dict[str, Any]]:
    """
    Get list of users who have been granted access to a project
    """
    owner_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Verify ownership
    project = supabase.table('projects').select('founder_id').eq('id', project_id).execute()
    if not project.data or project.data[0]['founder_id'] != owner_id:
        raise ValueError("Not authorized to view this information")
    
    grants = supabase.table('project_access_grants').select(
        '''
        id,
        granted_at,
        founders!project_access_grants_user_id_fkey(
            id, name, email, location, linkedin_url, linkedin_verified, profile_picture_url
        )
        '''
    ).eq('project_id', project_id).order('granted_at', desc=True).execute()
    
    result = []
    for grant in grants.data or []:
        user = grant.get('founders', {})
        result.append({
            'granted_at': grant['granted_at'],
            'user': {
                'id': user.get('id'),
                'name': user.get('name'),
                'email': user.get('email'),
                'location': user.get('location'),
                'linkedin_url': user.get('linkedin_url'),
                'linkedin_verified': user.get('linkedin_verified', False),
                'profile_picture_url': user.get('profile_picture_url')
            }
        })
    
    return result


def expire_old_requests() -> int:
    """
    Mark expired requests as expired (run periodically)
    
    Returns number of expired requests
    """
    supabase = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    
    result = supabase.table('project_access_requests').update({
        'status': 'expired'
    }).eq('status', 'pending').lt('expires_at', now).execute()
    
    count = len(result.data) if result.data else 0
    if count > 0:
        log_info(f"Expired {count} old access requests")
    
    return count


def get_pending_request_count(clerk_user_id: str) -> int:
    """Get count of pending requests for a project owner"""
    owner_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    result = supabase.table('project_access_requests').select(
        'id', count='exact'
    ).eq('owner_id', owner_id).eq('status', 'pending').execute()
    
    return result.count or 0
