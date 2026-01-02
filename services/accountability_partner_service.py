"""Accountability Partner service for managing partner profiles, requests, and workspace access"""
from config.database import get_supabase
from .notification_service import NotificationService
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

def _get_founder_id(clerk_user_id):
    """Helper to get founder ID from clerk_user_id"""
    supabase = get_supabase()
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    return user_profile.data[0]['id']

def _get_or_create_founder_id(clerk_user_id, user_name=None, user_email=None):
    """Helper to get founder ID from clerk_user_id, creating a minimal profile if needed for partners"""
    supabase = get_supabase()
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    
    if user_profile.data:
        return user_profile.data[0]['id']
    
    # Create minimal founder profile for accountability partners (required by schema)
    # Partners don't go through founder onboarding, so we create a minimal record
    founder_data = {
        'clerk_user_id': clerk_user_id,
        'name': user_name or 'Accountability Partner',
        'email': user_email or '',
        'purpose': 'both',  # Use valid purpose value (constraint requires: idea_needs_cofounder, skills_want_project, or both)
        'location': '',
        'looking_for': 'Accountability partner and advisor',
        'skills': [],
        'onboarding_completed': False,  # Partners don't complete founder onboarding
        'credits': 0  # Partners don't need credits
    }
    
    try:
        result = supabase.table('founders').insert(founder_data).execute()
        if not result.data:
            error_msg = "Failed to create founder profile for partner - no data returned"
            raise ValueError(error_msg)
        return result.data[0]['id']
    except Exception as e:
        error_msg = f"Failed to create founder profile for partner: {str(e)}"
        import traceback
        traceback.print_exc()
        raise ValueError(error_msg)

def create_partner_profile(clerk_user_id, data, user_name=None, user_email=None):
    """Create or update accountability partner profile"""
    
    # Create minimal founder profile if it doesn't exist (required by schema)
    try:
        founder_id = _get_or_create_founder_id(clerk_user_id, user_name, user_email)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise
    
    supabase = get_supabase()
    
    # Check if table exists
    try:
        test_query = supabase.table('accountability_partner_profiles').select('id').limit(1).execute()
    except Exception as e:
        error_msg = f"Accountability partner profiles feature is not yet available. Please run database migrations first. Error: {str(e)}"
        raise ValueError(error_msg)
    
    # Validate required fields
    if 'headline' not in data or data.get('headline') == '':
        raise ValueError("headline is required")
    
    # Check if profile exists
    try:
        existing = supabase.table('accountability_partner_profiles').select('id, status, max_active_workspaces').eq('user_id', founder_id).execute()
    except Exception as e:
        raise ValueError("Accountability partner profiles table not found. Please run database migrations.")
    
    # Handle max_active_workspaces - required for new profiles, optional for updates
    if existing.data:
        # Updating existing profile - use existing value if not provided
        max_workspaces = data.get('max_active_workspaces', existing.data[0].get('max_active_workspaces', 3))
    else:
        # Creating new profile - required
        if 'max_active_workspaces' not in data:
            raise ValueError("max_active_workspaces is required")
        max_workspaces = data['max_active_workspaces']
    
    # Validate max_active_workspaces
    try:
        max_workspaces = int(max_workspaces)
        if max_workspaces < 1 or max_workspaces > 10:
            raise ValueError("max_active_workspaces must be between 1 and 10")
    except (ValueError, TypeError) as e:
        if isinstance(e, ValueError) and "must be between" in str(e):
            raise
        raise ValueError(f"max_active_workspaces must be a number between 1 and 10. Received: {max_workspaces}")
    
    # Validate LinkedIn URL if provided
    linkedin_url = data.get('linkedin_url', '').strip()
    if linkedin_url and not (linkedin_url.startswith('http://') or linkedin_url.startswith('https://')):
        raise ValueError("LinkedIn URL must be a valid URL starting with http:// or https://")
    
    # Validate Twitter/X URL if provided
    twitter_url = data.get('twitter_url', '').strip()
    if twitter_url and not (twitter_url.startswith('http://') or twitter_url.startswith('https://')):
        raise ValueError("Twitter/X URL must be a valid URL starting with http:// or https://")
    
    profile_data = {
        'user_id': founder_id,
        'headline': data['headline'],
        'bio': data.get('bio', ''),
        'timezone': data.get('timezone', 'UTC'),
        'languages': data.get('languages', []),
        'expertise_stages': data.get('expertise_stages', []),
        'domains': data.get('domains', []),
        'max_active_workspaces': max_workspaces,
        'preferred_cadence': data.get('preferred_cadence', 'weekly'),
        'contact_email': data.get('contact_email'),
        'meeting_link': data.get('meeting_link'),
        'contact_note': data.get('contact_note'),
        'linkedin_url': linkedin_url if linkedin_url else None,
        'twitter_url': twitter_url if twitter_url else None,
    }
    
    try:
        if existing.data:
            # Update existing profile
            current_status = existing.data[0].get('status', 'PENDING')
            
            # If status is PENDING or REJECTED, keep it as PENDING (user is updating application)
            # If status is APPROVED, preserve it (admin approval should not be changed by user)
            if current_status in ('PENDING', 'REJECTED'):
                profile_data['status'] = 'PENDING'
                profile_data['is_discoverable'] = False  # Force false for pending/rejected
            else:
                # For APPROVED profiles, don't change status or is_discoverable
                # Only update other fields
                pass
            
            profile = supabase.table('accountability_partner_profiles').update(profile_data).eq('id', existing.data[0]['id']).execute()
        else:
            # Create new profile - force PENDING status and is_discoverable = false
            profile_data['status'] = 'PENDING'
            profile_data['is_discoverable'] = False
            profile = supabase.table('accountability_partner_profiles').insert(profile_data).execute()
        
        if not profile.data:
            error_msg = "Failed to create/update partner profile - no data returned"
            raise ValueError(error_msg)
        
        return profile.data[0]
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise ValueError(f"Failed to create/update partner profile: {str(e)}")

def update_partner_contact_info(clerk_user_id, contact_info):
    """Update contact info for partner profile"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Check if profile exists
    existing = supabase.table('accountability_partner_profiles').select('id').eq('user_id', founder_id).execute()
    if not existing.data:
        raise ValueError("Partner profile not found")
    
    update_data = {}
    if 'contact_email' in contact_info:
        update_data['contact_email'] = contact_info['contact_email']
    if 'meeting_link' in contact_info:
        update_data['meeting_link'] = contact_info['meeting_link']
    if 'contact_note' in contact_info:
        update_data['contact_note'] = contact_info['contact_note']
    
    if update_data:
        result = supabase.table('accountability_partner_profiles').update(update_data).eq('id', existing.data[0]['id']).execute()
        return result.data[0] if result.data else None
    
    return None

def get_partner_profile(clerk_user_id):
    """Get accountability partner profile for current user"""
    # For partners, founder profile might not exist yet if they just signed up
    # Try to get it, but don't fail if it doesn't exist (it will be created when profile is created)
    try:
        founder_id = _get_founder_id(clerk_user_id)
    except ValueError:
        # Founder profile doesn't exist yet - partner profile can't exist either
        return None
    
    supabase = get_supabase()
    
    try:
        profile = supabase.table('accountability_partner_profiles').select('*').eq('user_id', founder_id).execute()
    except Exception as e:
        # Table doesn't exist yet
        return None
    
    if not profile.data:
        return None
    
    profile_data = profile.data[0]
    
    # Calculate current_active_workspaces
    # Try to filter by role, but handle case where column might not exist
    try:
        active_workspaces = supabase.table('workspace_participants').select('workspace_id').eq(
            'user_id', founder_id
        ).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
    except Exception:
        # Fallback if role column doesn't exist yet - count all workspaces for this user
        active_workspaces = supabase.table('workspace_participants').select('workspace_id').eq(
            'user_id', founder_id
        ).execute()
    
    profile_data['current_active_workspaces'] = len(active_workspaces.data) if active_workspaces.data else 0
    
    return profile_data

def get_available_partners(workspace_id, filters=None, clerk_user_id=None):
    """Get available partners for marketplace, filtered by workspace attributes"""
    supabase = get_supabase()
    
    # Check if accountability_partner_profiles table exists
    try:
        # Try to query the table to see if it exists
        test_query = supabase.table('accountability_partner_profiles').select('id').limit(1).execute()
    except Exception as e:
        error_msg = str(e)
        if 'PGRST205' in error_msg or 'Could not find the table' in error_msg:
            raise ValueError(
                "The accountability_partner_profiles table does not exist in the database. "
                "Please run the database migration in Supabase SQL Editor: "
                "backend/migrations/001_create_accountability_partner_tables.sql"
            ) from e
        # Re-raise other exceptions
        raise
    
    # Get workspace info for filtering (only select stage, domain column may not exist)
    try:
        workspace = supabase.table('workspaces').select('stage, domain').eq('id', workspace_id).execute()
    except Exception:
        # Fallback if domain column doesn't exist
        workspace = supabase.table('workspaces').select('stage').eq('id', workspace_id).execute()
    
    workspace_data = workspace.data[0] if workspace.data else {}
    workspace_stage = workspace_data.get('stage', 'idea')
    workspace_domain = workspace_data.get('domain', '')
    
    # Build query for available partners - only APPROVED and discoverable
    query = supabase.table('accountability_partner_profiles').select(
        '*, user:founders!user_id(id, name, email)'
    ).eq('status', 'APPROVED').eq('is_discoverable', True)
    
    # Filter by expertise stages if workspace stage matches
    if workspace_stage:
        # Map workspace stages to partner expertise stages
        stage_mapping = {
            'idea': 'idea',
            'mvp': 'pre-seed',
            'revenue': 'seed',
            'other': 'idea'  # Default
        }
        mapped_stage = stage_mapping.get(workspace_stage, 'idea')
        query = query.contains('expertise_stages', [mapped_stage])
    
    # Filter by domains if workspace has domain and filter is requested
    if workspace_domain and filters and filters.get('domain'):
        query = query.contains('domains', [workspace_domain])
    
    try:
        profiles = query.execute()
    except Exception as e:
        error_msg = str(e)
        if 'PGRST205' in error_msg or 'Could not find the table' in error_msg:
            raise ValueError(
                "The accountability_partner_profiles table does not exist. "
                "Please run the database migration: backend/migrations/001_create_accountability_partner_tables.sql"
            ) from e
        raise
    except Exception as e:
        # Table or columns don't exist yet - return empty list
        return []
    
    # Get founder_id to check for existing requests
    try:
        founder_id = _get_founder_id(clerk_user_id)
    except ValueError:
        founder_id = None
    
    # Get existing requests for this workspace
    existing_requests = {}
    if founder_id:
        try:
            requests = supabase.table('partner_requests').select('partner_user_id, status').eq(
                'workspace_id', workspace_id
            ).eq('founder_user_id', founder_id).execute()
            if requests.data:
                for req in requests.data:
                    existing_requests[req['partner_user_id']] = req['status']
        except Exception:
            pass  # If table doesn't exist, continue without request status
    
    # Filter by capacity and format results
    available_partners = []
    for profile in (profiles.data or []):
        user_id = profile['user_id']
        
        # Check if this partner is already a founder/co-founder in this workspace
        # Partners cannot be partners for their own projects
        workspace_participant = supabase.table('workspace_participants').select('id, role').eq(
            'workspace_id', workspace_id
        ).eq('user_id', user_id).execute()
        
        if workspace_participant.data:
            participant_role = workspace_participant.data[0].get('role')
            # Skip if user is already a founder/co-founder (role is NULL or not ACCOUNTABILITY_PARTNER)
            if participant_role != 'ACCOUNTABILITY_PARTNER':
                continue  # Skip this partner - they're already a founder in this workspace
        
        # Count current active workspaces
        # Try to filter by role, but handle case where column might not exist
        try:
            active_count = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', user_id
            ).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
        except Exception:
            # Fallback if role column doesn't exist yet
            active_count = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', user_id
            ).execute()
        
        current_active = len(active_count.data) if active_count.data else 0
        max_active = profile.get('max_active_workspaces', 0)
        
        # Only include if has capacity
        if current_active < max_active:
            request_status = existing_requests.get(user_id)
            available_partners.append({
                **profile,
                'current_active_workspaces': current_active,
                'available_slots': max_active - current_active,
                'request_status': request_status  # 'PENDING', 'ACCEPTED', 'DECLINED', or None
            })
    
    return available_partners

def _verify_workspace_access(clerk_user_id, workspace_id):
    """Verify that the user is a participant in the workspace (founder or partner)"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    participant = supabase.table('workspace_participants').select('id, role').eq(
        'workspace_id', workspace_id
    ).eq('user_id', founder_id).execute()
    
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    return founder_id

def create_partner_request(clerk_user_id, workspace_id, partner_user_id):
    """Create a partner request from founder to partner"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    notification_service = NotificationService()
    
    # Prevent partners from being partners for their own workspaces
    # Check if the partner_user_id is already a founder/participant in this workspace
    existing_participant = supabase.table('workspace_participants').select('id, role').eq(
        'workspace_id', workspace_id
    ).eq('user_id', partner_user_id).execute()
    
    if existing_participant.data:
        # User is already a participant - check if they're a founder (not a partner)
        participant_role = existing_participant.data[0].get('role')
        if participant_role != 'ACCOUNTABILITY_PARTNER':
            raise ValueError("This user is already a founder/co-founder in this workspace. Accountability partners cannot be partners for their own projects.")
    
    # Check if partner profile exists
    try:
        partner_profile = supabase.table('accountability_partner_profiles').select('*').eq('user_id', partner_user_id).execute()
    except Exception as e:
        raise ValueError("Accountability partner profiles feature is not yet available. Please run database migrations first.")
    
    if not partner_profile.data:
        raise ValueError("Partner profile not found")
    
    # Check if partner has capacity
    # Try to filter by role, but handle case where column might not exist
    try:
        active_count = supabase.table('workspace_participants').select('workspace_id').eq(
            'user_id', partner_user_id
        ).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
    except Exception:
        # Fallback if role column doesn't exist yet
        active_count = supabase.table('workspace_participants').select('workspace_id').eq(
            'user_id', partner_user_id
        ).execute()
    
    current_active = len(active_count.data) if active_count.data else 0
    max_active = partner_profile.data[0].get('max_active_workspaces', 0)
    
    if current_active >= max_active:
        raise ValueError("Partner is at full capacity")
    
    # Check if request already exists
    existing = supabase.table('partner_requests').select('id').eq(
        'workspace_id', workspace_id
    ).eq('partner_user_id', partner_user_id).eq('status', 'PENDING').execute()
    
    if existing.data:
        raise ValueError("A pending request already exists for this partner")
    
    # Create request
    request_data = {
        'workspace_id': workspace_id,
        'founder_user_id': founder_id,
        'partner_user_id': partner_user_id,
        'status': 'PENDING'
    }
    
    partner_request = supabase.table('partner_requests').insert(request_data).execute()
    
    if not partner_request.data:
        raise ValueError("Failed to create partner request")
    
    # Notify partner (using generic event type to avoid enum constraint issues)
    try:
        notification_service.create_notification(
            workspace_id=workspace_id,
            recipient_id=partner_user_id,
            actor_id=founder_id,
            event_type='DECISION_CREATED',  # Using existing event type as fallback
            title=f"New accountability partner request for workspace",
            entity_type='partner_request',
            entity_id=partner_request.data[0]['id'],
            metadata={'workspace_id': workspace_id}
        )
    except Exception as e:
        # Log error but don't fail the request creation
        pass
    
    return partner_request.data[0]

def get_partner_requests(clerk_user_id, status=None):
    """Get partner requests for current user (as partner) with workspace details"""
    # For partners, founder profile might not exist yet
    try:
        founder_id = _get_founder_id(clerk_user_id)
    except ValueError:
        # Founder profile doesn't exist yet - no requests possible
        return []
    
    supabase = get_supabase()
    
    query = supabase.table('partner_requests').select(
        '*, workspace:workspaces!workspace_id(id, title, stage, match_id, project1_id, project2_id), founder:founders!founder_user_id(id, name)'
    ).eq('partner_user_id', founder_id)
    
    if status:
        query = query.eq('status', status)
    
    requests = query.order('created_at', desc=True).execute()
    
    # Enrich each request with workspace details
    enriched_requests = []
    for request in (requests.data or []):
        workspace_id = request.get('workspace_id')
        if workspace_id:
            # Get ALL KPIs (not just samples)
            kpis = supabase.table('workspace_kpis').select('id, label, status, target_value, target_date').eq('workspace_id', workspace_id).execute()
            kpi_summary = {
                'total': len(kpis.data) if kpis.data else 0,
                'not_started': len([k for k in (kpis.data or []) if k.get('status') == 'not_started']),
                'in_progress': len([k for k in (kpis.data or []) if k.get('status') == 'in_progress']),
                'done': len([k for k in (kpis.data or []) if k.get('status') == 'done']),
                'all': [{'label': k['label'], 'status': k['status'], 'target_value': k.get('target_value'), 'target_date': k.get('target_date')} for k in (kpis.data or [])]
            }
            
            # Get ALL decisions (not just samples)
            decisions = supabase.table('workspace_decisions').select('id, content, tag, created_at').eq('workspace_id', workspace_id).eq('is_active', True).order('created_at', desc=True).execute()
            decision_summary = {
                'total': len(decisions.data) if decisions.data else 0,
                'all': [{'content': d['content'], 'tag': d.get('tag', 'general'), 'created_at': d.get('created_at')} for d in (decisions.data or [])]
            }
            
            # Get ALL participants
            participants = supabase.table('workspace_participants').select('id, founders!workspace_participants_user_id_fkey(id, name)').eq('workspace_id', workspace_id).execute()
            participant_summary = {
                'total': len(participants.data) if participants.data else 0,
                'founders': [{'name': p.get('founders', {}).get('name', 'Unknown')} for p in (participants.data or [])]
            }
            
            # Get match/project info if available
            workspace_data = request.get('workspace', {})
            match_id = workspace_data.get('match_id') if isinstance(workspace_data, dict) else None
            project1_id = workspace_data.get('project1_id') if isinstance(workspace_data, dict) else None
            project2_id = workspace_data.get('project2_id') if isinstance(workspace_data, dict) else None
            
            # Fetch project details if project IDs exist
            projects_info = []
            if project1_id:
                project1 = supabase.table('projects').select('id, title, description, stage').eq('id', project1_id).execute()
                if project1.data:
                    projects_info.append(project1.data[0])
            
            if project2_id:
                project2 = supabase.table('projects').select('id, title, description, stage').eq('id', project2_id).execute()
                if project2.data:
                    projects_info.append(project2.data[0])
            
            # If no projects but match exists, try to get projects from match
            if not projects_info and match_id:
                match = supabase.table('matches').select('project1_id, project2_id').eq('id', match_id).execute()
                if match.data:
                    match_data = match.data[0]
                    if match_data.get('project1_id'):
                        project1 = supabase.table('projects').select('id, title, description, stage').eq('id', match_data['project1_id']).execute()
                        if project1.data:
                            projects_info.append(project1.data[0])
                    if match_data.get('project2_id'):
                        project2 = supabase.table('projects').select('id, title, description, stage').eq('id', match_data['project2_id']).execute()
                        if project2.data:
                            projects_info.append(project2.data[0])
            
            request['workspace_details'] = {
                'kpis': kpi_summary,
                'decisions': decision_summary,
                'participants': participant_summary,
                'match_id': match_id,
                'projects': projects_info
            }
        
        enriched_requests.append(request)
    
    return enriched_requests

def get_active_workspaces(clerk_user_id):
    """Get all active workspaces where the user is an accountability partner"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get all workspaces where user is a partner (role = ACCOUNTABILITY_PARTNER)
    try:
        participants = supabase.table('workspace_participants').select(
            'workspace_id, role, workspace:workspaces!workspace_id(id, title, stage, match_id, project1_id, project2_id)'
        ).eq('user_id', founder_id).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
    except Exception:
        # Fallback if role column doesn't exist yet - just get all workspaces
        participants = supabase.table('workspace_participants').select(
            'workspace_id, workspace:workspaces!workspace_id(id, title, stage, match_id, project1_id, project2_id)'
        ).eq('user_id', founder_id).execute()
    
    if not participants.data:
        return []
    
    # Format workspaces with project information
    workspaces = []
    for participant in participants.data:
        workspace = participant.get('workspace', {})
        if not workspace:
            continue
        
        workspace_id = workspace.get('id')
        if not workspace_id:
            continue
        
        # Get project information if available
        projects_info = []
        project1_id = workspace.get('project1_id')
        project2_id = workspace.get('project2_id')
        
        if project1_id:
            project1 = supabase.table('projects').select('id, title, description, stage').eq('id', project1_id).execute()
            if project1.data:
                projects_info.append(project1.data[0])
        
        if project2_id:
            project2 = supabase.table('projects').select('id, title, description, stage').eq('id', project2_id).execute()
            if project2.data:
                projects_info.append(project2.data[0])
        
        # If no projects but match exists, try to get projects from match
        if not projects_info and workspace.get('match_id'):
            match = supabase.table('matches').select('project1_id, project2_id').eq('id', workspace.get('match_id')).execute()
            if match.data:
                match_data = match.data[0]
                if match_data.get('project1_id'):
                    project1 = supabase.table('projects').select('id, title, description, stage').eq('id', match_data['project1_id']).execute()
                    if project1.data:
                        projects_info.append(project1.data[0])
                if match_data.get('project2_id'):
                    project2 = supabase.table('projects').select('id, title, description, stage').eq('id', match_data['project2_id']).execute()
                    if project2.data:
                        projects_info.append(project2.data[0])
        
        workspaces.append({
            'id': workspace_id,
            'title': workspace.get('title', 'Workspace'),
            'stage': workspace.get('stage', 'idea'),
            'projects': projects_info
        })
    
    return workspaces

def respond_to_partner_request(clerk_user_id, request_id, response):
    """Accept or decline a partner request"""
    if response not in ['accept', 'decline']:
        raise ValueError("response must be 'accept' or 'decline'")
    
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    notification_service = NotificationService()
    
    # Get request
    request_data = supabase.table('partner_requests').select('*').eq('id', request_id).execute()
    if not request_data.data:
        raise ValueError("Partner request not found")
    
    request_info = request_data.data[0]
    
    # Verify it's for this partner
    if request_info['partner_user_id'] != founder_id:
        raise ValueError("Unauthorized: This request is not for you")
    
    # Verify status is pending
    if request_info['status'] != 'PENDING':
        raise ValueError(f"Request is already {request_info['status']}")
    
    workspace_id = request_info['workspace_id']
    
    if response == 'accept':
        # Check capacity again
        try:
            partner_profile = supabase.table('accountability_partner_profiles').select('*').eq('user_id', founder_id).execute()
        except Exception as e:
            raise ValueError("Accountability partner profiles feature is not yet available. Please run database migrations first.")
        
        if not partner_profile.data:
            raise ValueError("Partner profile not found")
        
        # Try to filter by role, but handle case where column might not exist
        try:
            active_count = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', founder_id
            ).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
        except Exception:
            # Fallback if role column doesn't exist yet
            active_count = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', founder_id
            ).execute()
        
        current_active = len(active_count.data) if active_count.data else 0
        max_active = partner_profile.data[0].get('max_active_workspaces', 0)
        
        if current_active >= max_active:
            raise ValueError("You are at full capacity")
        
        # Add partner to workspace
        participant_data = {
            'workspace_id': workspace_id,
            'user_id': founder_id,
            'role': 'ACCOUNTABILITY_PARTNER'
        }
        
        # Check if already a participant
        existing = supabase.table('workspace_participants').select('id').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
        
        if existing.data:
            # Update role
            supabase.table('workspace_participants').update({'role': 'ACCOUNTABILITY_PARTNER'}).eq(
                'id', existing.data[0]['id']
            ).execute()
        else:
            # Add as participant
            supabase.table('workspace_participants').insert(participant_data).execute()
        
        # Update request status
        from datetime import datetime
        supabase.table('partner_requests').update({
            'status': 'ACCEPTED',
            'decided_at': datetime.now().isoformat()
        }).eq('id', request_id).execute()
        
        # Check if should auto-disable discoverability
        if current_active + 1 >= max_active:
            supabase.table('accountability_partner_profiles').update({
                'is_discoverable': False
            }).eq('user_id', founder_id).execute()
        
        # Notify founders
        participants = supabase.table('workspace_participants').select('user_id').eq(
            'workspace_id', workspace_id
        ).neq('role', 'ACCOUNTABILITY_PARTNER').execute()
        
        partner_name = supabase.table('founders').select('name').eq('id', founder_id).execute()
        partner_name_str = partner_name.data[0]['name'] if partner_name.data else 'Partner'
        
        for participant in (participants.data or []):
            try:
                notification_service.create_notification(
                    workspace_id=workspace_id,
                    recipient_id=participant['user_id'],
                    actor_id=founder_id,
                    event_type='DECISION_CREATED',  # Using existing event type as fallback
                    title=f"{partner_name_str} joined as accountability partner",
                    entity_type='workspace_participant',
                    entity_id=founder_id,
                    metadata={'workspace_id': workspace_id}
                )
            except Exception as e:
                pass
        
        return {'status': 'ACCEPTED', 'message': 'Partner request accepted'}
    
    else:  # decline
        from datetime import datetime
        supabase.table('partner_requests').update({
            'status': 'DECLINED',
            'decided_at': datetime.now().isoformat()
        }).eq('id', request_id).execute()
        
        # Notify founders
        participants = supabase.table('workspace_participants').select('user_id').eq(
            'workspace_id', workspace_id
        ).neq('role', 'ACCOUNTABILITY_PARTNER').execute()
        
        partner_name = supabase.table('founders').select('name').eq('id', founder_id).execute()
        partner_name_str = partner_name.data[0]['name'] if partner_name.data else 'Partner'
        
        for participant in (participants.data or []):
            try:
                notification_service.create_notification(
                    workspace_id=workspace_id,
                    recipient_id=participant['user_id'],
                    actor_id=founder_id,
                    event_type='DECISION_CREATED',  # Using existing event type as fallback
                    title=f"{partner_name_str} declined the accountability partner request",
                    entity_type='partner_request',
                    entity_id=request_id,
                    metadata={'workspace_id': workspace_id}
                )
            except Exception as e:
                pass
        
        return {'status': 'DECLINED', 'message': 'Partner request declined'}

def remove_partner_from_workspace(clerk_user_id, workspace_id, partner_user_id):
    """Remove a partner from workspace"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Verify partner is actually a partner
    # Try to filter by role, but handle case where column might not exist
    try:
        participant = supabase.table('workspace_participants').select('*').eq(
            'workspace_id', workspace_id
        ).eq('user_id', partner_user_id).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
    except Exception:
        # Fallback if role column doesn't exist yet - just check if user is a participant
        participant = supabase.table('workspace_participants').select('*').eq(
            'workspace_id', workspace_id
        ).eq('user_id', partner_user_id).execute()
    
    if not participant.data:
        raise ValueError("Partner not found in workspace")
    
    # Remove participant
    supabase.table('workspace_participants').delete().eq(
        'workspace_id', workspace_id
    ).eq('user_id', partner_user_id).execute()
    
    # Decrement current_active_workspaces and potentially re-enable discoverability
    try:
        partner_profile = supabase.table('accountability_partner_profiles').select('*').eq('user_id', partner_user_id).execute()
    except Exception:
        # Table doesn't exist - skip this step
        partner_profile = type('obj', (object,), {'data': None})()
    
    if partner_profile.data:
        # Try to filter by role, but handle case where column might not exist
        try:
            active_count = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', partner_user_id
            ).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
        except Exception:
            # Fallback if role column doesn't exist yet
            active_count = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', partner_user_id
            ).execute()
        
        current_active = len(active_count.data) if active_count.data else 0
        max_active = partner_profile.data[0].get('max_active_workspaces', 0)
        
        # If below capacity and was previously disabled, re-enable discoverability
        if current_active < max_active:
            supabase.table('accountability_partner_profiles').update({
                'is_discoverable': True
            }).eq('user_id', partner_user_id).execute()
    
    return {'status': 'removed', 'message': 'Partner removed from workspace'}

def compute_partner_impact_scorecard(clerk_user_id: str, workspace_id: str, partner_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Compute partner impact scorecard for a workspace.
    If partner_user_id is None, computes for the active partner in the workspace.
    """
    supabase = get_supabase()
    founder_id = _get_founder_id(clerk_user_id)
    
    # Verify workspace access
    participant = supabase.table('workspace_participants').select('id').eq('workspace_id', workspace_id).eq('user_id', founder_id).execute()
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    # Get partner user_id if not provided
    if not partner_user_id:
        partner_participant = supabase.table('workspace_participants').select('user_id, joined_at').eq(
            'workspace_id', workspace_id
        ).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
        
        if not partner_participant.data:
            return {
                'has_partner': False,
                'message': 'No accountability partner in this workspace'
            }
        
        partner_user_id = partner_participant.data[0]['user_id']
        partner_joined_at = partner_participant.data[0].get('joined_at')
    else:
        partner_participant = supabase.table('workspace_participants').select('joined_at').eq(
            'workspace_id', workspace_id
        ).eq('user_id', partner_user_id).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
        
        if not partner_participant.data:
            raise ValueError("Partner not found in this workspace")
        
        partner_joined_at = partner_participant.data[0].get('joined_at')
    
    # Get partner info
    partner_info = supabase.table('founders').select('id, name').eq('id', partner_user_id).execute()
    partner_name = partner_info.data[0]['name'] if partner_info.data else 'Partner'
    
    # Get partner profile for contact info
    partner_profile = supabase.table('accountability_partner_profiles').select(
        'contact_email, meeting_link, contact_note, timezone'
    ).eq('user_id', partner_user_id).execute()
    
    contact_info = {
        'email': partner_profile.data[0].get('contact_email') if partner_profile.data else None,
        'meeting_link': partner_profile.data[0].get('meeting_link') if partner_profile.data else None,
        'contact_note': partner_profile.data[0].get('contact_note') if partner_profile.data else None,
        'timezone': partner_profile.data[0].get('timezone', 'UTC') if partner_profile.data else 'UTC',
    }
    
    # Default email to founder email if not set
    if not contact_info['email']:
        founder_email = supabase.table('founders').select('email').eq('id', partner_user_id).execute()
        contact_info['email'] = founder_email.data[0].get('email') if founder_email.data else None
    
    # Parse partner joined date - ensure timezone-aware
    if partner_joined_at:
        if isinstance(partner_joined_at, str):
            partner_joined_at = datetime.fromisoformat(partner_joined_at.replace('Z', '+00:00'))
            # Ensure it's timezone-aware
            if partner_joined_at.tzinfo is None:
                partner_joined_at = partner_joined_at.replace(tzinfo=timezone.utc)
        elif isinstance(partner_joined_at, datetime):
            # Ensure it's timezone-aware
            if partner_joined_at.tzinfo is None:
                partner_joined_at = partner_joined_at.replace(tzinfo=timezone.utc)
        else:
            partner_joined_at = None
    else:
        partner_joined_at = None
    
    # Use UTC for all datetime operations
    now = datetime.now(timezone.utc)
    
    # Review period: last 12 weeks or since partner joined (whichever is shorter)
    review_weeks = 12
    if partner_joined_at:
        weeks_since_joined = (now - partner_joined_at).days / 7
        review_weeks = min(review_weeks, max(8, int(weeks_since_joined)))
    
    current_window_start = now - timedelta(weeks=review_weeks)
    baseline_window_start = current_window_start - timedelta(weeks=8) if partner_joined_at else None
    baseline_window_end = current_window_start if partner_joined_at else None
    
    def parse_datetime_safe(dt_str: str) -> Optional[datetime]:
        """Parse datetime string and ensure it's timezone-aware"""
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except:
            return None
    
    # 1. On-time check-ins calculation
    checkins = supabase.table('workspace_checkins').select('week_start, created_at').eq(
        'workspace_id', workspace_id
    ).gte('week_start', (current_window_start - timedelta(weeks=review_weeks)).isoformat()).execute()
    
    def is_checkin_ontime(checkin_week_start: str, checkin_created_at: str) -> bool:
        """Check if check-in was on time (before Monday 11:59 PM local time)"""
        try:
            week_start_str = checkin_week_start.replace('Z', '+00:00')
            created_at_str = checkin_created_at.replace('Z', '+00:00')
            
            week_start = datetime.fromisoformat(week_start_str)
            created_at = datetime.fromisoformat(created_at_str)
            
            # Ensure timezone-aware
            if week_start.tzinfo is None:
                week_start = week_start.replace(tzinfo=timezone.utc)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            
            # Monday 11:59 PM of the week
            monday_deadline = week_start + timedelta(days=7) - timedelta(minutes=1)
            
            return created_at <= monday_deadline
        except Exception as e:
            return False
    
    current_checkins = []
    for c in (checkins.data or []):
        week_start_dt = parse_datetime_safe(c.get('week_start', ''))
        if week_start_dt and week_start_dt >= current_window_start:
            current_checkins.append(c)
    
    baseline_checkins = []
    if baseline_window_start and baseline_window_end:
        for c in (checkins.data or []):
            week_start_dt = parse_datetime_safe(c.get('week_start', ''))
            if week_start_dt and baseline_window_start <= week_start_dt < baseline_window_end:
                baseline_checkins.append(c)
    
    current_ontime = sum(1 for c in current_checkins if is_checkin_ontime(c['week_start'], c['created_at']))
    baseline_ontime = sum(1 for c in baseline_checkins if is_checkin_ontime(c['week_start'], c['created_at']))
    
    current_scheduled_weeks = review_weeks
    baseline_scheduled_weeks = 8 if baseline_checkins else 0
    
    baseline_ontime_rate = (baseline_ontime / baseline_scheduled_weeks * 100) if baseline_scheduled_weeks > 0 else 0
    current_ontime_rate = (current_ontime / current_scheduled_weeks * 100) if current_scheduled_weeks > 0 else 0
    delta_ontime_rate = current_ontime_rate - baseline_ontime_rate
    
    # 2. Important tasks calculation
    tasks = supabase.table('workspace_tasks').select(
        'id, created_at, completed_at, status, kpi_id, decision_id'
    ).eq('workspace_id', workspace_id).execute()
    
    current_window_start_iso = current_window_start.isoformat()
    baseline_window_start_iso = baseline_window_start.isoformat() if baseline_window_start else None
    baseline_window_end_iso = baseline_window_end.isoformat() if baseline_window_end else None
    
    important_tasks_current = [t for t in (tasks.data or []) if 
                              (t.get('kpi_id') or t.get('decision_id')) and
                              t.get('created_at') and
                              t['created_at'] >= current_window_start_iso]
    
    important_tasks_done_current = [t for t in important_tasks_current if 
                                   t.get('status') == 'DONE' and
                                   t.get('completed_at') and
                                   t['completed_at'] >= current_window_start_iso]
    
    important_tasks_baseline = [t for t in (tasks.data or []) if 
                               baseline_window_start_iso and
                               (t.get('kpi_id') or t.get('decision_id')) and
                               t.get('created_at') and
                               baseline_window_start_iso <= t['created_at'] < baseline_window_end_iso]
    
    important_tasks_done_baseline = [t for t in important_tasks_baseline if 
                                    t.get('status') == 'DONE' and
                                    t.get('completed_at') and
                                    baseline_window_start_iso <= t.get('completed_at', '') < baseline_window_end_iso]
    
    important_task_completion_rate = (len(important_tasks_done_current) / max(1, len(important_tasks_current)) * 100) if important_tasks_current else 0
    important_tasks_per_week_current = len(important_tasks_done_current) / review_weeks if review_weeks > 0 else 0
    important_tasks_per_week_baseline = len(important_tasks_done_baseline) / 8 if baseline_window_start_iso and 8 > 0 else 0
    
    # 3. KPI trajectory calculation
    # Note: workspace_kpis doesn't have current_value, so we use status to estimate progress
    kpis = supabase.table('workspace_kpis').select(
        'id, label, target_value, target_date, status, created_at'
    ).eq('workspace_id', workspace_id).order('created_at', desc=False).execute()
    
    # Get primary KPIs (top 3-5, or all if less than 5)
    primary_kpis = (kpis.data or [])[:5]
    
    kpi_progresses = []
    for kpi in primary_kpis:
        status = kpi.get('status', 'not_started')
        target_value = kpi.get('target_value')
        
        # Map status to progress percentage since we don't have current_value
        # not_started = 0%, in_progress = 50%, done = 100%
        status_to_progress = {
            'not_started': 0,
            'in_progress': 50,
            'done': 100
        }
        
        progress_pct = status_to_progress.get(status, 0)
        
        # If KPI was created before partner joined, calculate progress since join
        kpi_created_at = None
        if kpi.get('created_at'):
            kpi_created_at = parse_datetime_safe(kpi['created_at'])
        
        # If partner joined after KPI was created, we can't measure progress since join
        # So we use current status as a proxy
        # For more accurate tracking, we'd need KPI history/snapshots
        if partner_joined_at and kpi_created_at:
            if kpi_created_at < partner_joined_at:
                # KPI existed before partner joined - use current status as progress
                # This is an approximation since we don't have historical values
                kpi_progresses.append(progress_pct)
            else:
                # KPI created after partner joined - use current status
                kpi_progresses.append(progress_pct)
        else:
            # No partner join date or KPI creation date - use current status
            kpi_progresses.append(progress_pct)
    
    avg_kpi_progress_pct = sum(kpi_progresses) / len(kpi_progresses) if kpi_progresses else 0
    
    # 4. Composite Partner Score (internal, normalized to 0-100)
    normalized_ontime = min(100, max(0, current_ontime_rate))
    normalized_tasks = min(100, max(0, important_task_completion_rate))
    normalized_kpi = min(100, max(0, avg_kpi_progress_pct))
    
    partner_score = (
        0.35 * normalized_ontime +
        0.35 * normalized_tasks +
        0.30 * normalized_kpi
    )
    
    return {
        'has_partner': True,
        'partner': {
            'id': partner_user_id,
            'name': partner_name,
            'joined_at': partner_joined_at.isoformat() if partner_joined_at else None,
        },
        'contact_info': contact_info,
        'metrics': {
            'on_time_checkins': {
                'baseline_rate': round(baseline_ontime_rate, 1),
                'current_rate': round(current_ontime_rate, 1),
                'delta': round(delta_ontime_rate, 1),
                'baseline_count': baseline_ontime,
                'current_count': current_ontime,
                'baseline_total': baseline_scheduled_weeks,
                'current_total': current_scheduled_weeks,
            },
            'important_tasks': {
                'completion_rate': round(important_task_completion_rate, 1),
                'per_week_current': round(important_tasks_per_week_current, 1),
                'per_week_baseline': round(important_tasks_per_week_baseline, 1),
                'delta': round(important_tasks_per_week_current - important_tasks_per_week_baseline, 1),
                'total_created': len(important_tasks_current),
                'total_done': len(important_tasks_done_current),
            },
            'kpi_progress': {
                'average_progress_pct': round(avg_kpi_progress_pct, 1),
                'primary_kpis_count': len(primary_kpis),
                'kpis_tracked': len(kpi_progresses),
            },
            'composite_score': round(partner_score, 1),  # Internal use
        },
        'review_period_weeks': review_weeks,
    }

def save_quarterly_review(clerk_user_id: str, workspace_id: str, partner_user_id: str, quarter: int, value_rating: int, continue_next_quarter: bool) -> Dict[str, Any]:
    """Save quarterly review from founder"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Verify workspace access
    participant = supabase.table('workspace_participants').select('id').eq('workspace_id', workspace_id).eq('user_id', founder_id).execute()
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    # Verify partner exists in workspace
    partner = supabase.table('workspace_participants').select('id').eq(
        'workspace_id', workspace_id
    ).eq('user_id', partner_user_id).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
    
    if not partner.data:
        raise ValueError("Partner not found in this workspace")
    
    # Upsert review
    review_data = {
        'workspace_id': workspace_id,
        'partner_user_id': partner_user_id,
        'quarter': quarter,
        'value_rating': value_rating,
        'continue_next_quarter': continue_next_quarter,
    }
    
    existing = supabase.table('quarterly_reviews').select('id').eq(
        'workspace_id', workspace_id
    ).eq('partner_user_id', partner_user_id).eq('quarter', quarter).execute()
    
    if existing.data:
        result = supabase.table('quarterly_reviews').update(review_data).eq('id', existing.data[0]['id']).execute()
    else:
        result = supabase.table('quarterly_reviews').insert(review_data).execute()
    
    if not result.data:
        raise ValueError("Failed to save quarterly review")
    
    return result.data[0]
