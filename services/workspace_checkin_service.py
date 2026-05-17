"""
Workspace Check-in Service

Handles scheduled check-ins for workspaces, including:
- 1-week check-in email after workspace creation
- Future: Monthly check-ins, milestone reminders, etc.
"""

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from utils.supabase_client import get_supabase
from utils.logger import log_info, log_error
from services import email_service


def get_workspaces_needing_week_one_checkin() -> List[Dict[str, Any]]:
    """
    Get workspaces that:
    1. Were created exactly 7 days ago (within a 24-hour window)
    2. Haven't received a week-one check-in email yet
    
    Returns list of workspaces with participant info
    """
    supabase = get_supabase()
    
    # Calculate the date range (7 days ago, with a 24-hour window)
    now = datetime.now(timezone.utc)
    week_ago_start = now - timedelta(days=8)  # 8 days ago
    week_ago_end = now - timedelta(days=7)    # 7 days ago
    
    # Get workspaces created 7-8 days ago that haven't had week_one_checkin_sent
    workspaces = supabase.table('workspaces').select(
        '''id, title, created_at, week_one_checkin_sent,
        participants:workspace_participants(
            user_id,
            user:founders!user_id(id, name, email, clerk_user_id)
        )'''
    ).gte('created_at', week_ago_start.isoformat()
    ).lt('created_at', week_ago_end.isoformat()
    ).or_('week_one_checkin_sent.is.null,week_one_checkin_sent.eq.false'
    ).execute()
    
    return workspaces.data or []


def send_week_one_checkins() -> Dict[str, Any]:
    """
    Send week-one check-in emails for all eligible workspaces.
    
    Returns summary of emails sent.
    """
    workspaces = get_workspaces_needing_week_one_checkin()
    
    results = {
        'workspaces_processed': 0,
        'emails_sent': 0,
        'errors': []
    }
    
    supabase = get_supabase()
    
    for workspace in workspaces:
        workspace_id = workspace['id']
        workspace_title = workspace.get('title') or 'Your Workspace'
        participants = workspace.get('participants') or []
        
        # Filter out advisors and get founders
        founders = [p for p in participants if p.get('user')]
        
        if len(founders) < 2:
            log_info(f"Skipping workspace {workspace_id}: not enough founders")
            continue
        
        results['workspaces_processed'] += 1
        
        # Send email to each founder
        for i, participant in enumerate(founders):
            user = participant.get('user', {})
            email = user.get('email')
            name = user.get('name', 'there')
            
            # Get partner name (the other founder)
            partner_idx = 1 if i == 0 else 0
            partner = founders[partner_idx].get('user', {}) if partner_idx < len(founders) else {}
            partner_name = partner.get('name', 'your co-founder')
            
            if not email:
                log_info(f"Skipping founder {user.get('id')}: no email")
                continue
            
            try:
                success = email_service.send_workspace_week_one_checkin_email(
                    to_email=email,
                    user_name=name.split()[0] if name else 'there',
                    partner_name=partner_name,
                    workspace_title=workspace_title,
                    workspace_id=workspace_id
                )
                
                if success:
                    results['emails_sent'] += 1
                    log_info(f"Sent week-one checkin to {email} for workspace {workspace_id}")
            except Exception as e:
                error_msg = f"Failed to send email to {email}: {str(e)}"
                results['errors'].append(error_msg)
                log_error(error_msg)
        
        # Mark workspace as having received the check-in
        try:
            supabase.table('workspaces').update({
                'week_one_checkin_sent': True
            }).eq('id', workspace_id).execute()
        except Exception as e:
            log_error(f"Failed to mark workspace {workspace_id} as checked-in: {e}")
    
    log_info(f"Week-one checkin complete: {results}")
    return results


def send_week_one_checkin_for_workspace(workspace_id: str) -> Dict[str, Any]:
    """
    Manually trigger week-one check-in for a specific workspace.
    Useful for testing or re-sending.
    """
    supabase = get_supabase()
    
    workspace = supabase.table('workspaces').select(
        '''id, title, created_at,
        participants:workspace_participants(
            user_id,
            user:founders!user_id(id, name, email, clerk_user_id)
        )'''
    ).eq('id', workspace_id).execute()
    
    if not workspace.data:
        return {'error': 'Workspace not found'}
    
    ws = workspace.data[0]
    workspace_title = ws.get('title') or 'Your Workspace'
    participants = ws.get('participants') or []
    founders = [p for p in participants if p.get('user')]
    
    results = {'emails_sent': 0, 'errors': []}
    
    for i, participant in enumerate(founders):
        user = participant.get('user', {})
        email = user.get('email')
        name = user.get('name', 'there')
        
        partner_idx = 1 if i == 0 else 0
        partner = founders[partner_idx].get('user', {}) if partner_idx < len(founders) else {}
        partner_name = partner.get('name', 'your co-founder')
        
        if not email:
            continue
        
        try:
            success = email_service.send_workspace_week_one_checkin_email(
                to_email=email,
                user_name=name.split()[0] if name else 'there',
                partner_name=partner_name,
                workspace_title=workspace_title,
                workspace_id=workspace_id
            )
            
            if success:
                results['emails_sent'] += 1
        except Exception as e:
            results['errors'].append(str(e))
    
    # Mark as sent
    supabase.table('workspaces').update({
        'week_one_checkin_sent': True
    }).eq('id', workspace_id).execute()
    
    return results
