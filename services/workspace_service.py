"""Workspace-related business logic"""
from config.database import get_supabase
from .notification_service import NotificationService, ApprovalService

def _get_founder_id(clerk_user_id, email=None):
    """Helper to get founder ID from clerk_user_id.
    If not found by clerk_user_id and email is provided, checks for existing founder by email
    and updates clerk_user_id to link accounts.
    """
    supabase = get_supabase()
    user_profile = supabase.table('founders').select('id, email').eq('clerk_user_id', clerk_user_id).execute()
    
    if not user_profile.data:
        # If email is provided, check for existing founder by email (case-insensitive)
        if email and email.strip():
            email_lower = email.strip().lower()
            all_founders = supabase.table('founders').select('id, email, clerk_user_id').execute()
            if all_founders.data:
                for founder in all_founders.data:
                    founder_email = founder.get('email', '').strip().lower()
                    if founder_email == email_lower:
                        # Found existing founder with same email - update clerk_user_id
                        supabase.table('founders').update({'clerk_user_id': clerk_user_id}).eq('id', founder['id']).execute()
                        return founder['id']
        
        raise ValueError("Profile not found")
    return user_profile.data[0]['id']

def _verify_workspace_access(clerk_user_id, workspace_id, allowed_roles=None):
    """Verify that the user is a participant in the workspace
    allowed_roles: list of roles allowed (None means any role is allowed)
    """
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Try to select role column, but handle case where it might not exist yet
    try:
        participant_query = supabase.table('workspace_participants').select('id, role').eq('workspace_id', workspace_id).eq('user_id', founder_id)
        participant = participant_query.execute()
    except Exception:
        # Fallback if role column doesn't exist yet
        participant_query = supabase.table('workspace_participants').select('id').eq('workspace_id', workspace_id).eq('user_id', founder_id)
        participant = participant_query.execute()
    
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    # Check role if specified
    if allowed_roles:
        participant_role = participant.data[0].get('role')
        # If role is None/not set, treat as founder (has all permissions)
        if participant_role is not None and participant_role not in allowed_roles:
            raise ValueError(f"Access denied: This action requires one of these roles: {', '.join(allowed_roles)}")
    
    return founder_id

def _can_edit_workspace(clerk_user_id, workspace_id):
    """Check if user can edit workspace (not ACCOUNTABILITY_PARTNER)"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Try to select role column, but handle case where it might not exist yet
    try:
        participant = supabase.table('workspace_participants').select('role').eq('workspace_id', workspace_id).eq('user_id', founder_id).execute()
    except Exception:
        # Fallback if role column doesn't exist yet - assume founder (can edit)
        participant = supabase.table('workspace_participants').select('id').eq('workspace_id', workspace_id).eq('user_id', founder_id).execute()
    
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    role = participant.data[0].get('role')
    # If role is None/not set, treat as founder (can edit)
    if role == 'ACCOUNTABILITY_PARTNER':
        raise ValueError("Access denied: Accountability partners cannot edit workspace settings")
    
    return founder_id

def _log_audit(workspace_id, user_id, action, entity_type=None, entity_id=None, metadata=None):
    """Log an audit entry for workspace mutations"""
    supabase = get_supabase()
    supabase.table('workspace_audit_log').insert({
        'workspace_id': workspace_id,
        'user_id': user_id,
        'action': action,
        'entity_type': entity_type,
        'entity_id': entity_id,
        'metadata': metadata
    }).execute()

def create_workspace_for_match(match_id, founder1_clerk_id=None, founder2_clerk_id=None):
    """Auto-create workspace when a match is created
    Now supports project-based workspaces where the workspace is tied to specific projects
    Checks plan limits before creating workspace
    """
    supabase = get_supabase()
    
    # Check if workspace already exists
    existing = supabase.table('workspaces').select('id').eq('match_id', match_id).execute()
    if existing.data:
        return existing.data[0]['id']
    
    # Get match to find founders and project (one project, two founders)
    match = supabase.table('matches').select('founder1_id, founder2_id, project_id').eq('id', match_id).execute()
    if not match.data:
        raise ValueError("Match not found")
    
    match_data = match.data[0]
    founder1_id = match_data['founder1_id']
    founder2_id = match_data['founder2_id']
    project_id = match_data.get('project_id')
    
    # Validate project exists if project_id is provided
    if project_id:
        project_check = supabase.table('projects').select('id').eq('id', project_id).execute()
        if not project_check.data:
            raise ValueError(f"Project {project_id} not found - cannot create workspace")
    
    # Check workspace limits for both founders
    # Fetch clerk IDs from founder IDs if not provided
    try:
        from services import plan_service
        
        # Get clerk IDs for both founders
        founders = supabase.table('founders').select('id, clerk_user_id').in_('id', [founder1_id, founder2_id]).execute()
        founder1_clerk_id = None
        founder2_clerk_id = None
        
        if founders.data:
            for founder in founders.data:
                if founder['id'] == founder1_id:
                    founder1_clerk_id = founder.get('clerk_user_id')
                elif founder['id'] == founder2_id:
                    founder2_clerk_id = founder.get('clerk_user_id')
        
        # Check limits for founder1
        if founder1_clerk_id:
            can_create, current_count, max_allowed = plan_service.check_workspace_limit(founder1_clerk_id)
            if not can_create:
                raise ValueError(f"Workspace limit reached. You have {current_count} workspaces (max: {max_allowed}). Upgrade to Pro or Pro+ for more workspaces.")
        
        # Check limits for founder2
        if founder2_clerk_id:
            can_create, current_count, max_allowed = plan_service.check_workspace_limit(founder2_clerk_id)
            if not can_create:
                raise ValueError(f"Workspace limit reached. You have {current_count} workspaces (max: {max_allowed}). Upgrade to Pro or Pro+ for more workspaces.")
    except ImportError:
        pass  # Plan service not available, skip check
    except ValueError:
        raise  # Re-raise ValueError (limit exceeded)
    except Exception as e:
        # If check fails for other reasons, log but don't block workspace creation (graceful degradation)
        pass
    
    # Create workspace with project information if available (one project, two founders)
    workspace_data = {
        'match_id': match_id,
        'stage': 'idea'
    }
    
    # Add project_id if this is a project-based match
    # After migration 006_add_project_id_to_workspaces.sql, this column will exist
    if project_id:
        workspace_data['project_id'] = project_id
    
    workspace = supabase.table('workspaces').insert(workspace_data).execute()
    
    if not workspace.data:
        raise ValueError("Failed to create workspace")
    
    workspace_id = workspace.data[0]['id']
    
    # Create participants
    try:
        participants_result = supabase.table('workspace_participants').insert([
            {'workspace_id': workspace_id, 'user_id': founder1_id},
            {'workspace_id': workspace_id, 'user_id': founder2_id}
        ]).execute()
    except Exception as e:
        # Don't fail the whole function if participants fail - workspace is still created
        pass
    
    return workspace_id

def list_user_workspaces(clerk_user_id):
    """Get all workspaces for a user with project and founder information"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get all workspaces where user is a participant
    participants = supabase.table('workspace_participants').select('workspace_id').eq('user_id', founder_id).execute()
    
    if not participants.data:
        return []
    
    workspace_ids = [p['workspace_id'] for p in participants.data]
    
    # Get workspace details with project and match info (one project, two founders)
    # Get project_id from matches table since workspaces doesn't have project_id column yet
    workspaces = supabase.table('workspaces').select(
        '*, match:matches!match_id(founder1_id, founder2_id, project_id, project:projects!project_id(*, founder:founders!founder_id(id, name, clerk_user_id)))'
    ).in_('id', workspace_ids).execute()
    
    if not workspaces.data:
        return []
    
    # Collect all founder IDs from matches to fetch in a single query (optimized)
    founder_ids_to_fetch = set()
    for workspace in workspaces.data:
        match = workspace.get('match', {})
        if match:
            if match.get('founder1_id'):
                founder_ids_to_fetch.add(match['founder1_id'])
            if match.get('founder2_id'):
                founder_ids_to_fetch.add(match['founder2_id'])
    
    # Fetch all founders in a single query
    founders_map = {}
    if founder_ids_to_fetch:
        founders_list = list(founder_ids_to_fetch)
        founders_result = supabase.table('founders').select('id, name, email').in_('id', founders_list).execute()
        if founders_result.data:
            for founder in founders_result.data:
                founders_map[founder['id']] = founder
    
    # Format workspaces with project and founder info (one project, two founders)
    formatted_workspaces = []
    for workspace in workspaces.data:
        match = workspace.get('match', {})
        # Get project from match (since workspaces doesn't have project_id column yet)
        project = match.get('project') if match else None
        
        # Determine the other founder from the match (using pre-fetched founders map)
        other_founder = None
        if match:
            founder1_id = match.get('founder1_id')
            founder2_id = match.get('founder2_id')
            
            if founder1_id == founder_id and founder2_id:
                # Current user is founder1, so other founder is founder2
                other_founder = founders_map.get(founder2_id)
            elif founder2_id == founder_id and founder1_id:
                # Current user is founder2, so other founder is founder1
                other_founder = founders_map.get(founder1_id)
        
        # Build project title (one project, two founders)
        project_title = None
        founder_name = None
        
        if project:
            project_title = project.get('title', 'Untitled Project')
            if project.get('founder'):
                founder_name = project['founder'].get('name', 'Unknown')
        
        # If no project, use workspace title or default
        if not project_title:
            project_title = workspace.get('title', 'Collaboration')
        
        formatted_workspaces.append({
            'id': workspace['id'],
            'title': workspace.get('title', 'Collaboration'),
            'project_title': project_title,
            'founder_names': [founder_name] if founder_name else [other_founder.get('name', 'Unknown')] if other_founder else ['Unknown'],
            'stage': workspace.get('stage', 'idea'),
            'created_at': workspace.get('created_at'),
            'project': project,
            'match_id': workspace.get('match_id'),
            'other_founder': other_founder
        })
    
    return formatted_workspaces

def get_workspace(clerk_user_id, workspace_id):
    """Get workspace overview with participants, equity summary, and KPI summary
    
    Optimized to fetch only necessary fields. These queries cannot be combined into a single
    query because participants, equity scenarios, and KPIs are separate tables with no direct
    JOIN relationship - they're all related only via workspace_id.
    """
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get workspace
    workspace = supabase.table('workspaces').select('*').eq('id', workspace_id).execute()
    if not workspace.data:
        raise ValueError("Workspace not found")
    
    workspace_data = workspace.data[0]
    
    # Fetch participants, equity, and KPIs
    # Note: These are separate queries because they're different tables that can't be JOINed
    # We optimize by fetching only the fields we need
    
    # Get participants with user info (using JOIN to avoid N+1)
    participants = supabase.table('workspace_participants').select('*, user:founders!user_id(id, name, email)').eq('workspace_id', workspace_id).execute()
    
    # Get current equity scenario (only one, filtered by is_current)
    equity = supabase.table('workspace_equity_scenarios').select('*').eq('workspace_id', workspace_id).eq('is_current', True).limit(1).execute()
    current_equity = equity.data[0] if equity.data else None
    
    # Get KPI summary - optimized to fetch only status field (not full records)
    kpis = supabase.table('workspace_kpis').select('status').eq('workspace_id', workspace_id).execute()
    
    # Calculate KPI summary from fetched data
    kpi_data = kpis.data if kpis.data else []
    kpi_summary = {
        'total': len(kpi_data),
        'not_started': len([k for k in kpi_data if k.get('status') == 'not_started']),
        'in_progress': len([k for k in kpi_data if k.get('status') == 'in_progress']),
        'done': len([k for k in kpi_data if k.get('status') == 'done'])
    }
    
    return {
        'id': workspace_data['id'],
        'match_id': workspace_data['match_id'],
        'title': workspace_data.get('title'),
        'stage': workspace_data.get('stage'),
        'created_at': workspace_data['created_at'],
        'updated_at': workspace_data['updated_at'],
        'participants': [{
            'user_id': p['user_id'],
            'user': p.get('user', {}),
            'role': p.get('role'),  # ACCOUNTABILITY_PARTNER or None (founder)
            'role_label': p.get('role_label'),
            'weekly_commitment_hours': p.get('weekly_commitment_hours'),
            'timezone': p.get('timezone')
        } for p in (participants.data or [])],
        'current_equity': current_equity,
        'kpi_summary': kpi_summary
    }

def update_workspace(clerk_user_id, workspace_id, data):
    """Update workspace title and stage"""
    _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    update_data = {}
    if 'title' in data:
        update_data['title'] = data['title']
    if 'stage' in data:
        if data['stage'] not in ['idea', 'mvp', 'revenue', 'other']:
            raise ValueError("Invalid stage. Must be one of: idea, mvp, revenue, other")
        update_data['stage'] = data['stage']
    
    if not update_data:
        raise ValueError("No valid fields to update")
    
    workspace = supabase.table('workspaces').update(update_data).eq('id', workspace_id).execute()
    
    if not workspace.data:
        raise ValueError("Workspace not found")
    
    founder_id = _get_founder_id(clerk_user_id)
    _log_audit(workspace_id, founder_id, 'update_workspace', 'workspace', workspace_id, update_data)
    
    # Return the full workspace object with all related data
    return get_workspace(clerk_user_id, workspace_id)

def get_participants(clerk_user_id, workspace_id):
    """Get all participants for a workspace"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    participants = supabase.table('workspace_participants').select('*, user:founders!user_id(id, name, email)').eq('workspace_id', workspace_id).execute()
    
    return [{
        'id': p['id'],
        'user_id': p['user_id'],
        'user': p.get('user', {}),
        'role': p.get('role'),  # ACCOUNTABILITY_PARTNER or None (founder)
        'role_label': p.get('role_label'),
        'weekly_commitment_hours': p.get('weekly_commitment_hours'),
        'timezone': p.get('timezone'),
        'created_at': p['created_at'],
        'updated_at': p['updated_at']
    } for p in (participants.data or [])]

def update_participant(clerk_user_id, workspace_id, user_id, data):
    """Update participant role_label, weekly_commitment_hours"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Check if participant is an accountability partner - they cannot be updated via this endpoint
    # Accountability partners should remain as accountability partners only
    try:
        participant_check = supabase.table('workspace_participants').select('role').eq(
            'workspace_id', workspace_id
        ).eq('user_id', user_id).execute()
    except Exception:
        # Fallback if role column doesn't exist yet
        participant_check = supabase.table('workspace_participants').select('id').eq(
            'workspace_id', workspace_id
        ).eq('user_id', user_id).execute()
    
    if not participant_check.data:
        raise ValueError("Participant not found")
    
    # Prevent updating accountability partners - they are not co-founders
    if participant_check.data[0].get('role') == 'ACCOUNTABILITY_PARTNER':
        raise ValueError("Accountability partners cannot be updated through this endpoint. They remain as accountability partners only.")
    
    update_data = {}
    if 'role_label' in data:
        update_data['role_label'] = data['role_label']
    if 'weekly_commitment_hours' in data:
        update_data['weekly_commitment_hours'] = data['weekly_commitment_hours']
    if 'timezone' in data:
        update_data['timezone'] = data['timezone']
    
    if not update_data:
        raise ValueError("No valid fields to update")
    
    participant = supabase.table('workspace_participants').update(update_data).eq('workspace_id', workspace_id).eq('user_id', user_id).execute()
    
    if not participant.data:
        raise ValueError("Participant not found")
    
    founder_id = _get_founder_id(clerk_user_id)
    _log_audit(workspace_id, founder_id, 'update_participant', 'workspace_participant', participant.data[0]['id'], update_data)
    
    # Return complete participant data with user info
    updated_participant = supabase.table('workspace_participants').select('*, user:founders!user_id(id, name, email)').eq('id', participant.data[0]['id']).execute()
    
    if updated_participant.data:
        return updated_participant.data[0]
    else:
        raise ValueError("Failed to update participant")
    return participant.data[0]

def get_decisions(clerk_user_id, workspace_id, tag=None, page=1, limit=20):
    """Get decisions for a workspace, optionally filtered by tag"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    query = supabase.table('workspace_decisions').select('*, creator:founders!created_by_user_id(id, name)').eq('workspace_id', workspace_id).eq('is_active', True)
    
    if tag:
        if tag not in ['equity', 'roles', 'scope', 'timeline', 'money', 'other']:
            raise ValueError("Invalid tag")
        query = query.eq('tag', tag)
    
    # Pagination
    from_index = (page - 1) * limit
    to_index = from_index + limit - 1
    
    decisions = query.order('created_at', desc=True).range(from_index, to_index).execute()
    
    return [{
        'id': d['id'],
        'workspace_id': d['workspace_id'],
        'created_by_user_id': d['created_by_user_id'],
        'creator': d.get('creator', {}),
        'tag': d['tag'],
        'content': d['content'],
        'is_active': d['is_active'],
        'created_at': d['created_at'],
        'updated_at': d['updated_at']
    } for d in (decisions.data or [])]

def create_decision(clerk_user_id, workspace_id, data):
    """Create a new decision (partners can comment but not create)"""
    founder_id = _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    notification_service = NotificationService()
    approval_service = ApprovalService()
    
    if 'tag' not in data or 'content' not in data:
        raise ValueError("tag and content are required")
    
    if data['tag'] not in ['equity', 'roles', 'scope', 'timeline', 'money', 'other']:
        raise ValueError("Invalid tag")
    
    if len(data['content']) > 1000:
        raise ValueError("Content must be 1000 characters or less")
    
    # Check if decision requires approval
    requires_approval = data.get('requires_approval', False)
    
    decision_data = {
        'workspace_id': workspace_id,
        'created_by_user_id': founder_id,
        'tag': data['tag'],
        'content': data['content'],
        'is_active': True,
        'requires_approval': requires_approval
    }
    
    if requires_approval:
        decision_data['approval_status'] = 'PENDING'
    
    decision = supabase.table('workspace_decisions').insert(decision_data).execute()
    
    if not decision.data:
        raise ValueError("Failed to create decision")
    
    decision_id = decision.data[0]['id']
    
    # Fetch the created decision with creator relationship
    decision_with_creator = supabase.table('workspace_decisions').select('*, creator:founders!created_by_user_id(id, name)').eq('id', decision_id).execute()
    
    _log_audit(workspace_id, founder_id, 'create_decision', 'workspace_decision', decision_id, {'tag': data['tag']})
    
    # If requires approval, create approval request
    if requires_approval:
        approval_id = approval_service.create_approval(
            clerk_user_id=clerk_user_id,
            workspace_id=workspace_id,
            entity_type='DECISION',
            entity_id=decision_id,
            proposed_data=decision_data
        )
        
        # Update decision with approval ID
        supabase.table('workspace_decisions').update({
            'approval_id': approval_id
        }).eq('id', decision_id).execute()
    else:
        # Send regular notification for non-approval decisions
        participants = supabase.table('workspace_participants').select('user_id, founders!workspace_participants_user_id_fkey(name)').eq('workspace_id', workspace_id).execute()
        creator = next((p['founders']['name'] for p in participants.data if p['user_id'] == founder_id), 'Someone')
        
        for participant in participants.data or []:
            if participant['user_id'] != founder_id:
                notification_service.create_notification(
                    workspace_id=workspace_id,
                    recipient_id=participant['user_id'],
                    actor_id=founder_id,
                    event_type='DECISION_CREATED',
                    title=f"{creator} added decision: {data['tag']}",
                    entity_type='workspace_decision',
                    entity_id=decision_id,
                    metadata={'tag': data['tag'], 'content': data['content'][:100]}
                )
    
    return {
        'id': decision_with_creator.data[0]['id'],
        'workspace_id': decision_with_creator.data[0]['workspace_id'],
        'created_by_user_id': decision_with_creator.data[0]['created_by_user_id'],
        'creator': decision_with_creator.data[0].get('creator', {}),
        'tag': decision_with_creator.data[0]['tag'],
        'content': decision_with_creator.data[0]['content'],
        'is_active': decision_with_creator.data[0]['is_active'],
        'created_at': decision_with_creator.data[0]['created_at'],
        'updated_at': decision_with_creator.data[0]['updated_at']
    }

def update_decision(clerk_user_id, decision_id, data):
    """Update a decision (with short edit window - 5 minutes)"""
    supabase = get_supabase()
    
    # Get decision to verify access
    decision = supabase.table('workspace_decisions').select('*, workspace:workspaces!workspace_id(id)').eq('id', decision_id).execute()
    if not decision.data:
        raise ValueError("Decision not found")
    
    decision_data = decision.data[0]
    workspace_id = decision_data['workspace']['id']
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    
    # Check edit window (5 minutes)
    from datetime import datetime, timezone
    created_at = datetime.fromisoformat(decision_data['created_at'].replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    if (now - created_at).total_seconds() > 300:  # 5 minutes
        raise ValueError("Edit window has expired (5 minutes)")
    
    # Only creator can edit
    if decision_data['created_by_user_id'] != founder_id:
        raise ValueError("Only the creator can edit this decision")
    
    update_data = {}
    if 'content' in data:
        if len(data['content']) > 1000:
            raise ValueError("Content must be 1000 characters or less")
        update_data['content'] = data['content']
    if 'tag' in data:
        if data['tag'] not in ['equity', 'roles', 'scope', 'timeline', 'money', 'other']:
            raise ValueError("Invalid tag")
        update_data['tag'] = data['tag']
    
    if not update_data:
        raise ValueError("No valid fields to update")
    
    updated = supabase.table('workspace_decisions').update(update_data).eq('id', decision_id).execute()
    
    _log_audit(workspace_id, founder_id, 'update_decision', 'workspace_decision', decision_id, update_data)
    
    return updated.data[0]

def get_equity_scenarios(clerk_user_id, workspace_id):
    """Get all equity scenarios and current scenario"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    scenarios = supabase.table('workspace_equity_scenarios').select('*, creator:founders!created_by_user_id(id, name)').eq('workspace_id', workspace_id).order('created_at', desc=True).execute()
    
    current = None
    all_scenarios = []
    
    for s in (scenarios.data or []):
        scenario = {
            'id': s['id'],
            'workspace_id': s['workspace_id'],
            'label': s['label'],
            'data': s['data'],
            'is_current': s['is_current'],
            'created_by_user_id': s['created_by_user_id'],
            'creator': s.get('creator', {}),
            'created_at': s['created_at'],
            'updated_at': s['updated_at'],
            'approval_status': s.get('approval_status', 'PENDING'),
            'status': s.get('status', 'active'),
            'note': s.get('note')
        }
        if s['is_current']:
            current = scenario
        all_scenarios.append(scenario)
    
    return {
        'scenarios': all_scenarios,
        'current': current
    }

def create_equity_scenario(clerk_user_id, workspace_id, data):
    """Create a new equity scenario (requires approval) - partners cannot create"""
    founder_id = _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    notification_service = NotificationService()
    approval_service = ApprovalService()
    
    if 'label' not in data or 'data' not in data:
        raise ValueError("label and data are required")
    
    # Validate data structure
    equity_data = data['data']
    if 'users' not in equity_data:
        raise ValueError("data.users is required")
    
    # Ensure accountability partners are not included in equity scenarios
    if equity_data.get('users'):
        # Get all participants to check their roles
        try:
            participants = supabase.table('workspace_participants').select('user_id, role').eq(
                'workspace_id', workspace_id
            ).execute()
        except Exception:
            # Fallback if role column doesn't exist yet
            participants = supabase.table('workspace_participants').select('user_id').eq(
                'workspace_id', workspace_id
            ).execute()
        
        # Create a map of user_id to role
        participant_roles = {}
        for p in (participants.data or []):
            participant_roles[p['user_id']] = p.get('role')
        
        # Filter out accountability partners from equity users
        equity_users = equity_data['users']
        filtered_users = [
            u for u in equity_users 
            if participant_roles.get(u.get('userId')) != 'ACCOUNTABILITY_PARTNER'
        ]
        
        if len(filtered_users) != len(equity_users):
            raise ValueError("Accountability partners cannot be included in equity scenarios. They are not co-founders.")
        
        equity_data['users'] = filtered_users
    
    # Set is_current if specified, otherwise False
    is_current = data.get('is_current', False)
    
    # Create scenario with pending approval status
    scenario_data = {
        'workspace_id': workspace_id,
        'label': data['label'],
        'data': equity_data,
        'is_current': False,  # Can't be current until approved
        'created_by_user_id': founder_id,
        'approval_status': 'PENDING',
        'status': 'active'  # New scenarios start as active (pending approval)
    }
    
    scenario = supabase.table('workspace_equity_scenarios').insert(scenario_data).execute()
    
    if not scenario.data:
        raise ValueError("Failed to create equity scenario")
    
    scenario_id = scenario.data[0]['id']
    
    # Create approval request
    proposed_data = {
        'label': data['label'],
        'data': equity_data,
        'is_current': is_current
    }
    
    
    try:
        approval_id = approval_service.create_approval(
            clerk_user_id=clerk_user_id,
            workspace_id=workspace_id,
            entity_type='EQUITY_SCENARIO',
            entity_id=scenario_id,
            proposed_data=proposed_data
        )
    except Exception as e:
        # Clean up the scenario if approval fails
        supabase.table('workspace_equity_scenarios').delete().eq('id', scenario_id).execute()
        raise e
    
    # Update scenario with approval ID
    supabase.table('workspace_equity_scenarios').update({
        'approval_id': approval_id
    }).eq('id', scenario_id).execute()
    
    _log_audit(workspace_id, founder_id, 'create_equity_scenario', 'workspace_equity_scenario', scenario_id, {'label': data['label'], 'requires_approval': True})
    
    return {**scenario.data[0], 'approval_id': approval_id, 'approval_status': 'PENDING'}

def set_current_equity_scenario(clerk_user_id, scenario_id):
    """Set an equity scenario as current (requires approval)"""
    supabase = get_supabase()
    approval_service = ApprovalService()
    
    # Get scenario to verify access
    scenario = supabase.table('workspace_equity_scenarios').select('*').eq('id', scenario_id).execute()
    if not scenario.data:
        raise ValueError("Equity scenario not found")
    
    workspace_id = scenario.data[0]['workspace_id']
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    
    # Check if scenario already has pending approval
    current_approval_status = scenario.data[0].get('approval_status')
    if current_approval_status == 'PENDING':
        # Return the existing pending status instead of creating a new approval
        return {
            **scenario.data[0],
            'message': 'This equity scenario already has a pending approval'
        }
    
    # If already current, no need for approval
    if scenario.data[0].get('is_current'):
        return {
            **scenario.data[0],
            'message': 'This scenario is already current'
        }
    
    # Only allow setting as current if scenario is approved
    if current_approval_status != 'APPROVED':
        raise ValueError("Only approved scenarios can be set as current")
    
    # Set all other scenarios to not current and mark as canceled
    supabase.table('workspace_equity_scenarios').update({
        'is_current': False,
        'status': 'canceled'
    }).eq('workspace_id', workspace_id).neq('id', scenario_id).execute()
    
    # Set this scenario as current and active
    updated = supabase.table('workspace_equity_scenarios').update({
        'is_current': True,
        'status': 'active'
    }).eq('id', scenario_id).execute()
    
    _log_audit(workspace_id, founder_id, 'set_current_equity_scenario', 'workspace_equity_scenario', scenario_id)
    
    return updated.data[0]
    
    # Otherwise, create approval request for setting as current
    original_data = {
        'is_current': scenario.data[0].get('is_current', False)
    }
    proposed_data = {
        'is_current': True,
        'label': scenario.data[0]['label'],
        'data': scenario.data[0]['data']
    }
    
    approval_id = approval_service.create_approval(
        clerk_user_id=clerk_user_id,
        workspace_id=workspace_id,
        entity_type='EQUITY_SCENARIO',
        entity_id=scenario_id,
        proposed_data=proposed_data,
        original_data=original_data
    )
    
    # Update scenario to pending status
    updated = supabase.table('workspace_equity_scenarios').update({
        'approval_status': 'PENDING',
        'approval_id': approval_id
    }).eq('id', scenario_id).execute()
    
    _log_audit(workspace_id, founder_id, 'request_set_current_equity_scenario', 'workspace_equity_scenario', scenario_id, {'requires_approval': True})
    
    return {**updated.data[0], 'approval_id': approval_id, 'approval_status': 'PENDING'}

def update_equity_scenario_note(clerk_user_id, scenario_id, note):
    """Update the note for an equity scenario"""
    
    supabase = get_supabase()
    
    # Get scenario to verify access
    scenario = supabase.table('workspace_equity_scenarios').select('*').eq('id', scenario_id).execute()
    if not scenario.data:
        raise ValueError("Equity scenario not found")
    
    workspace_id = scenario.data[0]['workspace_id']
    
    _verify_workspace_access(clerk_user_id, workspace_id)
    
    # Validate note length
    if note and len(note) > 255:
        raise ValueError("Note must be 255 characters or less")
    
    # Update the note
    updated = supabase.table('workspace_equity_scenarios').update({'note': note or None}).eq('id', scenario_id).execute()
    
    founder_id = _get_founder_id(clerk_user_id)
    
    _log_audit(workspace_id, founder_id, 'update_equity_scenario_note', 'workspace_equity_scenario', scenario_id, {'note': note})
    
    return updated.data[0]

def generate_agreement_draft(clerk_user_id, workspace_id):
    """Generate a founders' agreement draft based on current equity and roles"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get workspace info
    workspace = supabase.table('workspaces').select('*').eq('id', workspace_id).execute()
    if not workspace.data:
        raise ValueError("Workspace not found")
    
    workspace_data = workspace.data[0]
    
    # Get current equity scenario
    equity_scenario = supabase.table('workspace_equity_scenarios').select('*').eq('workspace_id', workspace_id).eq('is_current', True).execute()
    if not equity_scenario.data:
        raise ValueError("No current equity scenario found. Please set a current equity scenario before generating a draft.")
    
    current_equity = equity_scenario.data[0]
    equity_data = current_equity.get('data', {})
    
    # Get all workspace participants
    participants = supabase.table('workspace_participants').select('*, user:founders!user_id(id, name, email)').eq('workspace_id', workspace_id).execute()
    
    # Get all roles
    roles = supabase.table('workspace_roles').select('*, user:founders!user_id(id, name)').eq('workspace_id', workspace_id).execute()
    
    # Format equity owners
    # The equity data structure is: { users: [{ userId, percent }], vesting: { years, cliffMonths } }
    equity_owners = []
    users_data = equity_data.get('users', [])
    
    for participant in (participants.data or []):
        user = participant.get('user', {})
        user_id = user.get('id')
        
        # Find equity percentage for this user
        user_equity = None
        for user_equity_data in users_data:
            if user_equity_data.get('userId') == user_id:
                user_equity = user_equity_data.get('percent', 0)
                break
        
        if user_equity is not None:
            equity_owners.append({
                'userId': user_id,
                'name': user.get('name', 'Unknown'),
                'email': user.get('email', ''),
                'percent': user_equity
            })
    
    # Format roles
    formatted_roles = []
    for role in (roles.data or []):
        user = role.get('user', {})
        formatted_roles.append({
            'userId': user.get('id'),
            'name': user.get('name', 'Unknown'),
            'title': role.get('role_title', ''),
            'responsibilities': role.get('responsibilities', '')
        })
    
    # Generate the draft
    from datetime import datetime
    draft = {
        'workspaceName': workspace_data.get('title', 'Untitled Workspace'),
        'workspaceId': workspace_id,
        'generatedAt': datetime.utcnow().isoformat(),
        'createdAt': workspace_data.get('created_at'),
        'equity': {
            'vestingYears': equity_data.get('vesting', {}).get('years', 4),
            'cliffMonths': equity_data.get('vesting', {}).get('cliffMonths', 12),
            'owners': equity_owners
        },
        'roles': formatted_roles,
        'disclaimer': 'This is a non-binding summary of your founders\' agreement terms. It is not a legal contract. Please consult with a lawyer before finalizing any legal agreements.'
    }
    
    return draft

def get_roles(clerk_user_id, workspace_id):
    """Get all roles for a workspace"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    roles = supabase.table('workspace_roles').select('*, user:founders!user_id(id, name)').eq('workspace_id', workspace_id).execute()
    
    return [{
        'id': r['id'],
        'workspace_id': r['workspace_id'],
        'user_id': r['user_id'],
        'user': r.get('user', {}),
        'role_title': r['role_title'],
        'responsibilities': r.get('responsibilities'),
        'created_at': r['created_at'],
        'updated_at': r['updated_at']
    } for r in (roles.data or [])]

def upsert_role(clerk_user_id, workspace_id, user_id, data):
    """Upsert role and responsibilities for a user"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Prevent adding accountability partners to roles - they are not co-founders
    try:
        participant_check = supabase.table('workspace_participants').select('role').eq(
            'workspace_id', workspace_id
        ).eq('user_id', user_id).execute()
    except Exception:
        # Fallback if role column doesn't exist yet
        participant_check = supabase.table('workspace_participants').select('id').eq(
            'workspace_id', workspace_id
        ).eq('user_id', user_id).execute()
    
    if participant_check.data and participant_check.data[0].get('role') == 'ACCOUNTABILITY_PARTNER':
        raise ValueError("Accountability partners cannot be assigned roles. They are not co-founders.")
    
    if 'role_title' not in data:
        raise ValueError("role_title is required")
    
    # Check if role exists
    existing = supabase.table('workspace_roles').select('id').eq('workspace_id', workspace_id).eq('user_id', user_id).execute()
    
    role_data = {
        'workspace_id': workspace_id,
        'user_id': user_id,
        'role_title': data['role_title'],
        'responsibilities': data.get('responsibilities')
    }
    
    if existing.data:
        # Update
        role = supabase.table('workspace_roles').update(role_data).eq('id', existing.data[0]['id']).execute()
    else:
        # Insert
        role = supabase.table('workspace_roles').insert(role_data).execute()
    
    if not role.data:
        raise ValueError("Failed to upsert role")
    
    founder_id = _get_founder_id(clerk_user_id)
    _log_audit(workspace_id, founder_id, 'upsert_role', 'workspace_role', role.data[0]['id'], {'user_id': user_id, 'role_title': data['role_title']})
    
    return role.data[0]

def get_kpis(clerk_user_id, workspace_id):
    """Get all KPIs for a workspace"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    kpis = supabase.table('workspace_kpis').select('*, owner:founders!owner_user_id(id, name)').eq('workspace_id', workspace_id).order('created_at', desc=True).execute()
    
    return [{
        'id': k['id'],
        'workspace_id': k['workspace_id'],
        'label': k['label'],
        'target_value': k.get('target_value'),
        'target_date': k.get('target_date'),
        'owner_user_id': k['owner_user_id'],
        'owner': k.get('owner', {}),
        'status': k.get('status', 'not_started'),
        'created_at': k['created_at'],
        'updated_at': k['updated_at']
    } for k in (kpis.data or [])]

def create_kpi(clerk_user_id, workspace_id, data):
    """Create a new KPI - partners can view but not create"""
    founder_id = _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    notification_service = NotificationService()
    
    if 'label' not in data:
        raise ValueError("label is required")
    
    kpi_data = {
        'workspace_id': workspace_id,
        'label': data['label'],
        'target_value': data.get('target_value'),
        'target_date': data.get('target_date'),
        'owner_user_id': data.get('owner_user_id', founder_id),  # Default to creator
        'status': data.get('status', 'not_started')
    }
    
    if kpi_data['status'] not in ['not_started', 'in_progress', 'done']:
        raise ValueError("Invalid status")
    
    kpi = supabase.table('workspace_kpis').insert(kpi_data).execute()
    
    if not kpi.data:
        raise ValueError("Failed to create KPI")
    
    kpi_id = kpi.data[0]['id']
    
    # Fetch the created KPI with owner relationship
    kpi_with_owner = supabase.table('workspace_kpis').select('*, owner:founders!owner_user_id(id, name)').eq('id', kpi_id).execute()
    
    _log_audit(workspace_id, founder_id, 'create_kpi', 'workspace_kpi', kpi_id, {'label': data['label']})
    
    # Send notification to other participants
    participants = supabase.table('workspace_participants').select('user_id, founders!workspace_participants_user_id_fkey(name)').eq('workspace_id', workspace_id).execute()
    creator = next((p['founders']['name'] for p in participants.data if p['user_id'] == founder_id), 'Someone')
    
    for participant in participants.data or []:
        if participant['user_id'] != founder_id:
            notification_service.create_notification(
                workspace_id=workspace_id,
                recipient_id=participant['user_id'],
                actor_id=founder_id,
                event_type='KPI_CREATED',
                title=f"{creator} created KPI: {data['label']}",
                entity_type='workspace_kpi',
                entity_id=kpi_id,
                metadata={'label': data['label']}
            )
    
    return {
        'id': kpi_with_owner.data[0]['id'],
        'workspace_id': kpi_with_owner.data[0]['workspace_id'],
        'label': kpi_with_owner.data[0]['label'],
        'target_value': kpi_with_owner.data[0].get('target_value'),
        'target_date': kpi_with_owner.data[0].get('target_date'),
        'owner_user_id': kpi_with_owner.data[0]['owner_user_id'],
        'owner': kpi_with_owner.data[0].get('owner', {}),
        'status': kpi_with_owner.data[0].get('status', 'not_started'),
        'created_at': kpi_with_owner.data[0]['created_at'],
        'updated_at': kpi_with_owner.data[0]['updated_at']
    }

def update_kpi(clerk_user_id, kpi_id, data):
    """Update KPI status/target - partners can view but not edit"""
    supabase = get_supabase()
    notification_service = NotificationService()
    
    # Get KPI to verify access
    kpi = supabase.table('workspace_kpis').select('*, workspace:workspaces!workspace_id(id)').eq('id', kpi_id).execute()
    if not kpi.data:
        raise ValueError("KPI not found")
    
    workspace_id = kpi.data[0]['workspace']['id']
    _can_edit_workspace(clerk_user_id, workspace_id)
    
    update_data = {}
    if 'status' in data:
        if data['status'] not in ['not_started', 'in_progress', 'done']:
            raise ValueError("Invalid status")
        update_data['status'] = data['status']
    if 'target_value' in data:
        update_data['target_value'] = data['target_value']
    if 'target_date' in data:
        update_data['target_date'] = data['target_date']
    if 'label' in data:
        update_data['label'] = data['label']
    if 'owner_user_id' in data:
        update_data['owner_user_id'] = data['owner_user_id']
    
    if not update_data:
        raise ValueError("No valid fields to update")
    
    updated = supabase.table('workspace_kpis').update(update_data).eq('id', kpi_id).execute()
    
    if not updated.data:
        raise ValueError("Failed to update KPI")
    
    # Fetch the updated KPI with owner relationship
    kpi_with_owner = supabase.table('workspace_kpis').select('*, owner:founders!owner_user_id(id, name)').eq('id', kpi_id).execute()
    
    founder_id = _get_founder_id(clerk_user_id)
    _log_audit(workspace_id, founder_id, 'update_kpi', 'workspace_kpi', kpi_id, update_data)
    
    # Send notification to other participants
    participants = supabase.table('workspace_participants').select('user_id, founders!workspace_participants_user_id_fkey(name)').eq('workspace_id', workspace_id).execute()
    updater = next((p['founders']['name'] for p in participants.data if p['user_id'] == founder_id), 'Someone')
    
    for participant in participants.data or []:
        if participant['user_id'] != founder_id:
            notification_service.create_notification(
                workspace_id=workspace_id,
                recipient_id=participant['user_id'],
                actor_id=founder_id,
                event_type='KPI_UPDATED',
                title=f"{updater} updated KPI: {kpi.data[0]['label']}",
                entity_type='workspace_kpi',
                entity_id=kpi_id,
                metadata=update_data
            )
    
    return {
        'id': kpi_with_owner.data[0]['id'],
        'workspace_id': kpi_with_owner.data[0]['workspace_id'],
        'label': kpi_with_owner.data[0]['label'],
        'target_value': kpi_with_owner.data[0].get('target_value'),
        'target_date': kpi_with_owner.data[0].get('target_date'),
        'owner_user_id': kpi_with_owner.data[0]['owner_user_id'],
        'owner': kpi_with_owner.data[0].get('owner', {}),
        'status': kpi_with_owner.data[0].get('status', 'not_started'),
        'created_at': kpi_with_owner.data[0]['created_at'],
        'updated_at': kpi_with_owner.data[0]['updated_at']
    }

def get_checkins(clerk_user_id, workspace_id, limit=3):
    """Get recent checkins for a workspace"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    checkins = supabase.table('workspace_checkins').select('*, creator:founders!created_by_user_id(id, name)').eq('workspace_id', workspace_id).order('week_start', desc=True).limit(limit).execute()
    
    return [{
        'id': c['id'],
        'workspace_id': c['workspace_id'],
        'week_start': c['week_start'],
        'summary': c.get('summary'),
        'status': c.get('status', 'on_track'),
        'progress_percent': c.get('progress_percent'),
        'created_by_user_id': c['created_by_user_id'],
        'creator': c.get('creator', {}),
        'created_at': c['created_at']
    } for c in (checkins.data or [])]

def create_checkin(clerk_user_id, workspace_id, data):
    """Create a new checkin - partners can view and comment but founders create"""
    founder_id = _can_edit_workspace(clerk_user_id, workspace_id)
    supabase = get_supabase()
    notification_service = NotificationService()
    
    # Get workspace title for notifications
    workspace = supabase.table('workspaces').select('title').eq('id', workspace_id).execute()
    workspace_title = workspace.data[0].get('title', 'workspace') if workspace.data else 'workspace'
    
    if 'week_start' not in data:
        raise ValueError("week_start is required")
    
    if 'status' not in data:
        raise ValueError("status is required")
    
    # Validate status
    valid_statuses = ['on_track', 'slightly_behind', 'off_track']
    if data['status'] not in valid_statuses:
        raise ValueError(f"status must be one of: {', '.join(valid_statuses)}")
    
    # Validate progress_percent if provided
    progress_percent = data.get('progress_percent')
    if progress_percent is not None:
        try:
            progress_percent = float(progress_percent)
            if progress_percent < 0 or progress_percent > 100:
                raise ValueError("progress_percent must be between 0 and 100")
        except (ValueError, TypeError):
            raise ValueError("progress_percent must be a number between 0 and 100")
    
    checkin = supabase.table('workspace_checkins').insert({
        'workspace_id': workspace_id,
        'week_start': data['week_start'],
        'summary': data.get('summary'),
        'status': data['status'],
        'progress_percent': progress_percent,
        'created_by_user_id': founder_id
    }).execute()
    
    if not checkin.data:
        raise ValueError("Failed to create checkin")
    
    # Fetch the newly created checkin with creator relationship
    new_checkin = supabase.table('workspace_checkins').select('*, creator:founders!created_by_user_id(id, name)').eq('id', checkin.data[0]['id']).single().execute()
    
    if not new_checkin.data:
        raise ValueError("Failed to retrieve created checkin with creator info")
    
    _log_audit(workspace_id, founder_id, 'create_checkin', 'workspace_checkin', new_checkin.data['id'])
    
    # Send notification to other participants (including partners)
    # Try to select role, but handle case where column might not exist
    try:
        participants = supabase.table('workspace_participants').select('user_id, role, founders!workspace_participants_user_id_fkey(name)').eq('workspace_id', workspace_id).execute()
    except Exception:
        # Fallback if role column doesn't exist yet
        participants = supabase.table('workspace_participants').select('user_id, founders!workspace_participants_user_id_fkey(name)').eq('workspace_id', workspace_id).execute()
    
    creator = next((p.get('founders', {}).get('name') for p in participants.data if p['user_id'] == founder_id), 'Someone')
    
    for participant in participants.data or []:
        if participant['user_id'] != founder_id:
            # Different notification for partners (only if role column exists)
            if participant.get('role') == 'ACCOUNTABILITY_PARTNER':
                notification_service.create_notification(
                    workspace_id=workspace_id,
                    recipient_id=participant['user_id'],
                    actor_id=founder_id,
                    event_type='CHECKIN_CREATED_FOR_REVIEW',
                    title=f"New check-in to review for {workspace_title}",
                    entity_type='workspace_checkin',
                    entity_id=new_checkin.data['id'],
                    metadata={'status': data['status'], 'progress': progress_percent, 'workspace_title': workspace_title}
                )
            else:
                notification_service.create_notification(
                    workspace_id=workspace_id,
                    recipient_id=participant['user_id'],
                    actor_id=founder_id,
                    event_type='CHECKIN_CREATED',
                    title=f"{creator} posted weekly check-in: {data['status'].replace('_', ' ').title()}",
                    entity_type='workspace_checkin',
                    entity_id=new_checkin.data['id'],
                    metadata={'status': data['status'], 'progress': progress_percent}
                )
    
    return new_checkin.data

def add_checkin_comment(clerk_user_id, checkin_id, comment):
    """Add a comment to a check-in (partners can comment)"""
    supabase = get_supabase()
    
    # Get checkin to verify access
    checkin = supabase.table('workspace_checkins').select('*, workspace:workspaces!workspace_id(id)').eq('id', checkin_id).execute()
    if not checkin.data:
        raise ValueError("Check-in not found")
    
    workspace_id = checkin.data[0]['workspace']['id']
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)  # Any participant can comment
    
    if len(comment) > 1000:
        raise ValueError("Comment must be 1000 characters or less")
    
    # Insert comment
    comment_data = {
        'checkin_id': checkin_id,
        'user_id': founder_id,
        'comment': comment
    }
    
    # Assuming we have a workspace_checkin_comments table
    comment_result = supabase.table('workspace_checkin_comments').insert(comment_data).execute()
    
    if not comment_result.data:
        raise ValueError("Failed to add comment")
    
    _log_audit(workspace_id, founder_id, 'add_checkin_comment', 'workspace_checkin_comment', comment_result.data[0]['id'])
    
    return comment_result.data[0]

def set_checkin_verdict(clerk_user_id, checkin_id, verdict):
    """Set verdict for a check-in (partners only)"""
    if verdict not in ['on_track', 'at_risk', 'off_track']:
        raise ValueError("verdict must be one of: on_track, at_risk, off_track")
    
    supabase = get_supabase()
    
    # Get checkin to verify access
    checkin = supabase.table('workspace_checkins').select('*, workspace:workspaces!workspace_id(id)').eq('id', checkin_id).execute()
    if not checkin.data:
        raise ValueError("Check-in not found")
    
    workspace_id = checkin.data[0]['workspace']['id']
    founder_id = _get_founder_id(clerk_user_id)
    
    # Verify user is a partner
    # Try to select role, but handle case where column might not exist
    try:
        participant = supabase.table('workspace_participants').select('role').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
    except Exception:
        # Fallback if role column doesn't exist yet
        participant = supabase.table('workspace_participants').select('id').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
    
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    role = participant.data[0].get('role')
    if role != 'ACCOUNTABILITY_PARTNER':
        raise ValueError("Only accountability partners can set verdicts")
    
    # Update or insert verdict
    # Assuming we have a workspace_checkin_verdicts table
    existing_verdict = supabase.table('workspace_checkin_verdicts').select('id').eq(
        'checkin_id', checkin_id
    ).eq('user_id', founder_id).execute()
    
    verdict_data = {
        'checkin_id': checkin_id,
        'user_id': founder_id,
        'verdict': verdict
    }
    
    if existing_verdict.data:
        verdict_result = supabase.table('workspace_checkin_verdicts').update(verdict_data).eq(
            'id', existing_verdict.data[0]['id']
        ).execute()
    else:
        verdict_result = supabase.table('workspace_checkin_verdicts').insert(verdict_data).execute()
    
    if not verdict_result.data:
        raise ValueError("Failed to set verdict")
    
    # Notify founders
    notification_service = NotificationService()
    participants = supabase.table('workspace_participants').select('user_id').eq(
        'workspace_id', workspace_id
    ).neq('role', 'ACCOUNTABILITY_PARTNER').execute()
    
    partner_name = supabase.table('founders').select('name').eq('id', founder_id).execute()
    partner_name_str = partner_name.data[0]['name'] if partner_name.data else 'Partner'
    
    for participant in (participants.data or []):
        notification_service.create_notification(
            workspace_id=workspace_id,
            recipient_id=participant['user_id'],
            actor_id=founder_id,
            event_type='CHECKIN_VERDICT_SET',
            title=f"{partner_name_str} set verdict: {verdict.replace('_', ' ').title()}",
            entity_type='workspace_checkin_verdict',
            entity_id=verdict_result.data[0]['id'],
            metadata={'checkin_id': checkin_id, 'verdict': verdict}
        )
    
    _log_audit(workspace_id, founder_id, 'set_checkin_verdict', 'workspace_checkin_verdict', verdict_result.data[0]['id'], {'verdict': verdict})
    
    return verdict_result.data[0]

def get_checkin_partner_review(clerk_user_id, workspace_id, checkin_id):
    """Get partner review for a check-in (partners only)"""
    supabase = get_supabase()
    
    # Get checkin to verify it exists
    checkin = supabase.table('workspace_checkins').select('*, workspace:workspaces!workspace_id(id)').eq('id', checkin_id).execute()
    if not checkin.data:
        raise ValueError("Check-in not found")
    
    # Verify workspace matches
    if checkin.data[0]['workspace']['id'] != workspace_id:
        raise ValueError("Check-in does not belong to this workspace")
    
    founder_id = _get_founder_id(clerk_user_id)
    
    # Verify user is a partner
    try:
        participant = supabase.table('workspace_participants').select('role').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
    except Exception:
        participant = supabase.table('workspace_participants').select('id').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
    
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    role = participant.data[0].get('role')
    if role != 'ACCOUNTABILITY_PARTNER':
        raise ValueError("Only accountability partners can view their reviews")
    
    # Get existing review
    review = supabase.table('workspace_checkin_partner_reviews').select('*').eq(
        'checkin_id', checkin_id
    ).eq('partner_user_id', founder_id).execute()
    
    if review.data:
        return review.data[0]
    return None

def upsert_checkin_partner_review(clerk_user_id, workspace_id, checkin_id, verdict, comment):
    """Create or update partner review for a check-in (partners only)"""
    if verdict not in ['ON_TRACK', 'AT_RISK', 'OFF_TRACK']:
        raise ValueError("verdict must be one of: ON_TRACK, AT_RISK, OFF_TRACK")
    
    if comment and len(comment) > 500:
        raise ValueError("comment must be 500 characters or less")
    
    supabase = get_supabase()
    notification_service = NotificationService()
    
    # Get checkin to verify it exists
    checkin = supabase.table('workspace_checkins').select('*, workspace:workspaces!workspace_id(id)').eq('id', checkin_id).execute()
    if not checkin.data:
        raise ValueError("Check-in not found")
    
    # Verify workspace matches
    if checkin.data[0]['workspace']['id'] != workspace_id:
        raise ValueError("Check-in does not belong to this workspace")
    
    founder_id = _get_founder_id(clerk_user_id)
    
    # Verify user is a partner
    try:
        participant = supabase.table('workspace_participants').select('role').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
    except Exception:
        participant = supabase.table('workspace_participants').select('id').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
    
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    role = participant.data[0].get('role')
    if role != 'ACCOUNTABILITY_PARTNER':
        raise ValueError("Only accountability partners can create/update reviews")
    
    # Check if review exists
    existing_review = supabase.table('workspace_checkin_partner_reviews').select('id').eq(
        'checkin_id', checkin_id
    ).eq('partner_user_id', founder_id).execute()
    
    review_data = {
        'checkin_id': checkin_id,
        'workspace_id': workspace_id,
        'partner_user_id': founder_id,
        'verdict': verdict,
        'comment': comment or None
    }
    
    if existing_review.data:
        # Update existing review
        review_result = supabase.table('workspace_checkin_partner_reviews').update(review_data).eq(
            'id', existing_review.data[0]['id']
        ).execute()
        is_new = False
    else:
        # Create new review
        review_result = supabase.table('workspace_checkin_partner_reviews').insert(review_data).execute()
        is_new = True
    
    if not review_result.data:
        raise ValueError("Failed to save review")
    
    # Notify founders
    participants = supabase.table('workspace_participants').select('user_id').eq(
        'workspace_id', workspace_id
    ).neq('role', 'ACCOUNTABILITY_PARTNER').execute()
    
    partner_name = supabase.table('founders').select('name').eq('id', founder_id).execute()
    partner_name_str = partner_name.data[0]['name'] if partner_name.data else 'Partner'
    
    # Map verdict to display text
    verdict_display = {
        'ON_TRACK': 'On track',
        'AT_RISK': 'At risk',
        'OFF_TRACK': 'Off track'
    }.get(verdict, verdict)
    
    for participant in (participants.data or []):
        notification_service.create_notification(
            workspace_id=workspace_id,
            recipient_id=participant['user_id'],
            actor_id=founder_id,
            event_type='CHECKIN_CREATED',  # Using existing event type
            title=f"{partner_name_str} reviewed this week's check-in: {verdict_display}",
            entity_type='workspace_checkin_partner_review',
            entity_id=review_result.data[0]['id'],
            metadata={'checkin_id': checkin_id, 'verdict': verdict, 'is_new': is_new}
        )
    
    _log_audit(workspace_id, founder_id, 'upsert_checkin_partner_review', 'workspace_checkin_partner_review', review_result.data[0]['id'], {'verdict': verdict, 'is_new': is_new})
    
    return review_result.data[0]

def get_checkin_partner_reviews_for_founders(clerk_user_id, workspace_id, checkin_id):
    """Get partner reviews for a check-in (founders can view)"""
    _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get checkin to verify it exists
    checkin = supabase.table('workspace_checkins').select('*, workspace:workspaces!workspace_id(id)').eq('id', checkin_id).execute()
    if not checkin.data:
        raise ValueError("Check-in not found")
    
    # Verify workspace matches
    if checkin.data[0]['workspace']['id'] != workspace_id:
        raise ValueError("Check-in does not belong to this workspace")
    
    # Get all partner reviews for this check-in with partner info
    reviews = supabase.table('workspace_checkin_partner_reviews').select(
        '*, partner:founders!partner_user_id(id, name)'
    ).eq('checkin_id', checkin_id).execute()
    
    return reviews.data or []

