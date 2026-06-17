"""Pro Trial Request Service - handles free trial requests and approvals"""
from config.database import get_supabase
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

TRIAL_DURATION_DAYS = 7


def _get_founder_id(clerk_user_id: str) -> str:
    """Get founder ID from clerk_user_id"""
    supabase = get_supabase()
    result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not result.data:
        raise ValueError("Founder not found")
    return result.data[0]['id']


def get_trial_status(clerk_user_id: str) -> Dict[str, Any]:
    """
    Get user's trial status.
    Returns info about pending/active/past trials.
    """
    try:
        founder_id = _get_founder_id(clerk_user_id)
    except ValueError:
        return {
            'has_active_trial': False,
            'has_pending_request': False,
            'has_used_trial': False,
            'trial_info': None,
        }
    
    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    
    # Get all trial requests for this user
    trials = supabase.table('pro_trial_requests').select('*').eq('founder_id', founder_id).order('created_at', desc=True).execute()
    
    if not trials.data:
        return {
            'has_active_trial': False,
            'has_pending_request': False,
            'has_used_trial': False,
            'trial_info': None,
        }
    
    has_active_trial = False
    has_pending_request = False
    has_used_trial = False
    active_trial_info = None
    
    for trial in trials.data:
        status = trial.get('status')
        trial_end = trial.get('trial_end')
        
        if status == 'pending':
            has_pending_request = True
        elif status == 'approved':
            has_used_trial = True
            if trial_end:
                try:
                    end_date = datetime.fromisoformat(trial_end.replace('Z', '+00:00'))
                    if end_date.tzinfo is None:
                        end_date = end_date.replace(tzinfo=timezone.utc)
                    if end_date > now:
                        has_active_trial = True
                        days_left = (end_date - now).days
                        active_trial_info = {
                            'id': trial.get('id'),
                            'trial_start': trial.get('trial_start'),
                            'trial_end': trial_end,
                            'days_remaining': max(0, days_left),
                        }
                except (ValueError, TypeError):
                    pass
        elif status == 'rejected':
            has_used_trial = True
    
    return {
        'has_active_trial': has_active_trial,
        'has_pending_request': has_pending_request,
        'has_used_trial': has_used_trial,
        'trial_info': active_trial_info,
    }


def has_active_trial(clerk_user_id: str) -> bool:
    """Quick check if user has an active trial (for plan gating)"""
    status = get_trial_status(clerk_user_id)
    return status.get('has_active_trial', False)


def submit_trial_request(clerk_user_id: str, reason: str) -> Dict[str, Any]:
    """
    Submit a trial request.
    Returns the created request or error.
    """
    if not reason or len(reason.strip()) < 20:
        raise ValueError("Please provide a more detailed reason (at least 20 characters)")
    
    if len(reason) > 1000:
        raise ValueError("Reason is too long (max 1000 characters)")
    
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Check if user already has pending or active trial
    status = get_trial_status(clerk_user_id)
    
    if status.get('has_active_trial'):
        raise ValueError("You already have an active trial")
    
    if status.get('has_pending_request'):
        raise ValueError("You already have a pending trial request")
    
    if status.get('has_used_trial'):
        raise ValueError("You have already used your free trial")
    
    # Check if user is already Pro
    founder = supabase.table('founders').select('plan').eq('id', founder_id).execute()
    if founder.data and founder.data[0].get('plan') in ['PRO', 'PRO_PLUS', 'PRO_TRIAL']:
        raise ValueError("You already have a Pro subscription")
    
    # Create the request
    result = supabase.table('pro_trial_requests').insert({
        'founder_id': founder_id,
        'reason': reason.strip(),
        'status': 'pending',
    }).execute()
    
    if not result.data:
        raise ValueError("Failed to submit trial request")
    
    return {
        'success': True,
        'message': 'Your trial request has been submitted. We will review it shortly.',
        'request_id': result.data[0]['id'],
    }


def get_pending_requests() -> List[Dict[str, Any]]:
    """Get all pending trial requests (for admin)"""
    supabase = get_supabase()
    
    # Get pending requests with founder info
    requests = supabase.table('pro_trial_requests').select(
        '*, founders!pro_trial_requests_founder_id_fkey(id, name, email, clerk_user_id, skills, location, created_at)'
    ).eq('status', 'pending').order('created_at', desc=False).execute()
    
    formatted = []
    for req in requests.data or []:
        founder = req.get('founders', {})
        formatted.append({
            'id': req.get('id'),
            'reason': req.get('reason'),
            'created_at': req.get('created_at'),
            'founder': {
                'id': founder.get('id'),
                'name': founder.get('name'),
                'email': founder.get('email'),
                'skills': founder.get('skills', []),
                'location': founder.get('location'),
                'joined': founder.get('created_at'),
            }
        })
    
    return formatted


def get_all_requests(limit: int = 50) -> List[Dict[str, Any]]:
    """Get all trial requests (for admin)"""
    supabase = get_supabase()
    
    requests = supabase.table('pro_trial_requests').select(
        '*, founders!pro_trial_requests_founder_id_fkey(id, name, email)'
    ).order('created_at', desc=True).limit(limit).execute()
    
    formatted = []
    for req in requests.data or []:
        founder = req.get('founders', {})
        formatted.append({
            'id': req.get('id'),
            'reason': req.get('reason'),
            'status': req.get('status'),
            'created_at': req.get('created_at'),
            'reviewed_at': req.get('reviewed_at'),
            'trial_start': req.get('trial_start'),
            'trial_end': req.get('trial_end'),
            'rejection_reason': req.get('rejection_reason'),
            'founder': {
                'id': founder.get('id'),
                'name': founder.get('name'),
                'email': founder.get('email'),
            }
        })
    
    return formatted


def approve_trial_request(request_id: str, admin_clerk_user_id: str) -> Dict[str, Any]:
    """Approve a trial request (admin only)"""
    supabase = get_supabase()
    
    # Get the request
    req = supabase.table('pro_trial_requests').select('*, founders!pro_trial_requests_founder_id_fkey(email, name)').eq('id', request_id).execute()
    
    if not req.data:
        raise ValueError("Trial request not found")
    
    request_data = req.data[0]
    
    if request_data.get('status') != 'pending':
        raise ValueError(f"Request is already {request_data.get('status')}")
    
    now = datetime.now(timezone.utc)
    trial_end = now + timedelta(days=TRIAL_DURATION_DAYS)
    
    # Update the request
    result = supabase.table('pro_trial_requests').update({
        'status': 'approved',
        'reviewed_by': admin_clerk_user_id,
        'reviewed_at': now.isoformat(),
        'trial_start': now.isoformat(),
        'trial_end': trial_end.isoformat(),
    }).eq('id', request_id).execute()
    
    if not result.data:
        raise ValueError("Failed to approve request")
    
    founder = request_data.get('founders', {})
    
    return {
        'success': True,
        'message': f"Trial approved for {founder.get('name') or founder.get('email')}",
        'trial_end': trial_end.isoformat(),
    }


def reject_trial_request(request_id: str, admin_clerk_user_id: str, rejection_reason: Optional[str] = None) -> Dict[str, Any]:
    """Reject a trial request (admin only)"""
    supabase = get_supabase()
    
    # Get the request
    req = supabase.table('pro_trial_requests').select('*, founders!pro_trial_requests_founder_id_fkey(email, name)').eq('id', request_id).execute()
    
    if not req.data:
        raise ValueError("Trial request not found")
    
    request_data = req.data[0]
    
    if request_data.get('status') != 'pending':
        raise ValueError(f"Request is already {request_data.get('status')}")
    
    now = datetime.now(timezone.utc)
    
    # Update the request
    result = supabase.table('pro_trial_requests').update({
        'status': 'rejected',
        'reviewed_by': admin_clerk_user_id,
        'reviewed_at': now.isoformat(),
        'rejection_reason': rejection_reason,
    }).eq('id', request_id).execute()
    
    if not result.data:
        raise ValueError("Failed to reject request")
    
    founder = request_data.get('founders', {})
    
    return {
        'success': True,
        'message': f"Trial rejected for {founder.get('name') or founder.get('email')}",
    }
