"""Project-related business logic"""
import traceback
from datetime import datetime, timezone
from config.database import get_supabase

def get_user_projects(clerk_user_id):
    """Get all projects for a user"""
    supabase = get_supabase()
    
    # Get founder ID
    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        raise ValueError("Founder profile not found")
    
    founder_id = founder.data[0]['id']
    
    # Get all projects for this founder
    projects = supabase.table('projects').select('*').eq('founder_id', founder_id).order('display_order').execute()
    return projects.data if projects.data else []

def create_project(clerk_user_id, data):
    """Create a new project for a founder - free for all plans (no credits required)"""
    supabase = get_supabase()
    
    # Get founder ID
    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        raise ValueError("Founder profile not found")
    
    founder_id = founder.data[0]['id']
    
    # Get current project count for ordering
    existing_projects = supabase.table('projects').select('display_order').eq('founder_id', founder_id).execute()
    max_order = max([p['display_order'] for p in existing_projects.data], default=-1) if existing_projects.data else -1
    
    project_data = {
        'founder_id': founder_id,
        'title': data.get('title'),
        'description': data.get('description'),
        'stage': data.get('stage'),
        'display_order': max_order + 1,
        'is_active': True,
        'seeking_cofounder': True
    }
    
    # Add genre if provided
    if 'genre' in data and data.get('genre'):
        project_data['genre'] = data['genre']
    
    # Add needed_skills if provided (array of skills the project needs)
    if 'needed_skills' in data and data.get('needed_skills'):
        project_data['needed_skills'] = data['needed_skills']
    
    # Add compatibility_answers if provided (for project-specific matching)
    if 'compatibility_answers' in data and data['compatibility_answers']:
        project_data['compatibility_answers'] = data['compatibility_answers']
    
    response = supabase.table('projects').insert(project_data).execute()
    return response.data[0]

def update_project(clerk_user_id, project_id, data):
    """Update a project"""
    supabase = get_supabase()
    
    # Verify project belongs to user
    project = supabase.table('projects').select('founder_id, seeking_cofounder, founders!inner(clerk_user_id)').eq('id', project_id).execute()
    if not project.data:
        raise ValueError("Project not found")
    
    old_seeking_status = project.data[0].get('seeking_cofounder', True)
    
    # Update project
    update_data = {}
    if 'title' in data:
        update_data['title'] = data['title']
    if 'description' in data:
        update_data['description'] = data['description']
    if 'stage' in data:
        update_data['stage'] = data['stage']
    if 'genre' in data:
        update_data['genre'] = data['genre']
    if 'needed_skills' in data:
        update_data['needed_skills'] = data['needed_skills']
    if 'compatibility_answers' in data:
        update_data['compatibility_answers'] = data['compatibility_answers']
    if 'seeking_cofounder' in data:
        update_data['seeking_cofounder'] = data['seeking_cofounder']
    if 'visibility_level' in data:
        update_data['visibility_level'] = data['visibility_level']
    if 'is_paused' in data:
        update_data['is_paused'] = data['is_paused']
        if data['is_paused']:
            update_data['paused_at'] = datetime.now(timezone.utc).isoformat()
        else:
            update_data['paused_at'] = None
    
    response = supabase.table('projects').update(update_data).eq('id', project_id).execute()
    
    # If project stopped seeking, auto-reject pending swipes
    new_seeking_status = update_data.get('seeking_cofounder', old_seeking_status)
    if old_seeking_status and not new_seeking_status:
        try:
            from services.project_cleanup_service import handle_project_stopped_seeking
            handle_project_stopped_seeking(project_id)
        except Exception as e:
            # Log but don't fail the update
            from utils.logger import log_error
            log_error(f"Failed to auto-reject swipes for project {project_id}", error=e)
    
    return response.data[0]

def delete_project(clerk_user_id, project_id):
    """Soft delete a project and clean up related data"""
    supabase = get_supabase()
    
    # Verify project belongs to user
    project = supabase.table('projects').select('founder_id, founders!inner(clerk_user_id)').eq('id', project_id).execute()
    if not project.data:
        raise ValueError("Project not found")
    
    # Soft delete project (triggers will handle cleanup)
    supabase.table('projects').update({
        'is_deleted': True,
        'deleted_at': datetime.now(timezone.utc).isoformat(),
        'seeking_cofounder': False,
        'is_active': False
    }).eq('id', project_id).execute()
    
    # Auto-reject pending right swipes for this project
    try:
        from services.project_cleanup_service import handle_project_stopped_seeking
        handle_project_stopped_seeking(project_id)
    except Exception as e:
        # Log but don't fail the deletion
        from utils.logger import log_error
        log_error(f"Failed to auto-reject swipes for deleted project {project_id}", error=e)
    
    return {"message": "Project deleted successfully"}

