"""Project-related business logic"""
import traceback
from config.database import get_supabase

CREDITS_PER_PROJECT = 5

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
    """Create a new project for a founder - deducts 5 credits"""
    supabase = get_supabase()
    
    # Get founder ID and credits
    founder = supabase.table('founders').select('id, credits').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        raise ValueError("Founder profile not found")
    
    founder_id = founder.data[0]['id']
    current_credits = founder.data[0].get('credits', 0)
    
    # Check if user has enough credits
    if current_credits < CREDITS_PER_PROJECT:
        raise ValueError(f"Insufficient credits. You need {CREDITS_PER_PROJECT} credits to create a project but only have {current_credits}.")
    
    # Deduct credits
    new_credits = current_credits - CREDITS_PER_PROJECT
    supabase.table('founders').update({'credits': new_credits}).eq('id', founder_id).execute()
    
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
    
    # Add compatibility_answers if provided (for project-specific matching)
    if 'compatibility_answers' in data and data['compatibility_answers']:
        project_data['compatibility_answers'] = data['compatibility_answers']
    
    response = supabase.table('projects').insert(project_data).execute()
    return response.data[0]

def update_project(clerk_user_id, project_id, data):
    """Update a project"""
    supabase = get_supabase()
    
    # Verify project belongs to user
    project = supabase.table('projects').select('founder_id, founders!inner(clerk_user_id)').eq('id', project_id).execute()
    if not project.data:
        raise ValueError("Project not found")
    
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
    if 'compatibility_answers' in data:
        update_data['compatibility_answers'] = data['compatibility_answers']
    
    response = supabase.table('projects').update(update_data).eq('id', project_id).execute()
    return response.data[0]

def delete_project(clerk_user_id, project_id):
    """Delete a project"""
    supabase = get_supabase()
    
    # Verify project belongs to user
    project = supabase.table('projects').select('founder_id, founders!inner(clerk_user_id)').eq('id', project_id).execute()
    if not project.data:
        raise ValueError("Project not found")
    
    # Delete project
    supabase.table('projects').delete().eq('id', project_id).execute()
    return {"message": "Project deleted successfully"}

