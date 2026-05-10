"""Service to handle project cleanup and auto-reject swipes when projects stop seeking"""
from config.database import get_supabase
from datetime import datetime, timezone
from utils.logger import log_info, log_error

def auto_reject_swipes_for_project(project_id: str, founder_id: str = None) -> int:
    """
    Auto-reject all pending right swipes for a project that stopped seeking co-founders.
    Returns the number of swipes auto-rejected.
    """
    supabase = get_supabase()
    
    # Get project info
    project = supabase.table('projects').select('id, title, founder_id').eq('id', project_id).execute()
    if not project.data:
        return 0
    
    project_info = project.data[0]
    project_title = project_info.get('title', 'this project')
    project_owner_id = founder_id or project_info.get('founder_id')
    
    # Get all pending right swipes for this project
    pending_swipes = supabase.table('swipes').select('id, swiper_id, swiped_id').eq('project_id', project_id).eq('swipe_type', 'right').execute()
    
    if not pending_swipes.data:
        return 0
    
    rejected_count = 0
    
    for swipe in pending_swipes.data:
        try:
            swiper_id = swipe['swiper_id']
            
            # Create a simple notification directly (without workspace_audit_log trigger)
            # Since this is not workspace-related, we use the general notifications table
            supabase.table('notifications').insert({
                'user_id': swiper_id,
                'actor_user_id': project_owner_id,
                'type': 'PROJECT_UNAVAILABLE',
                'title': f"Project '{project_title}' is no longer available",
                'message': 'The project owner has closed applications.',
                'entity_type': 'project',
                'entity_id': project_id,
                'metadata': {
                    'project_id': project_id,
                    'project_title': project_title,
                    'reason': 'project_stopped_seeking',
                }
            }).execute()
            
            rejected_count += 1
            log_info(f"Notified swiper about closed project {project_id}")
            
        except Exception as e:
            # Don't fail the whole operation if notification fails
            log_error(f"Failed to notify swiper {swipe.get('swiper_id')} about closed project", error=e)
    
    return rejected_count

def handle_project_stopped_seeking(project_id: str) -> dict:
    """
    Handle when a project stops seeking co-founders.
    Auto-rejects pending swipes and sends notifications.
    """
    rejected_count = auto_reject_swipes_for_project(project_id)
    
    return {
        'project_id': project_id,
        'swipes_auto_rejected': rejected_count,
        'message': f'Auto-rejected {rejected_count} pending swipe(s) for this project'
    }
