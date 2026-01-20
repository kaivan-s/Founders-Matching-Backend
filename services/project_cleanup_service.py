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
    
    # Create notifications for users whose swipes were auto-rejected
    from services.notification_service import NotificationService
    notification_service = NotificationService()
    
    for swipe in pending_swipes.data:
        try:
            swiper_id = swipe['swiper_id']
            
            # Create notification for swiper
            notification_service.create_notification(
                workspace_id=None,
                recipient_id=swiper_id,
                actor_id=project_owner_id,
                event_type='SWIPE_REJECTED',
                title=f"Project '{project_title}' is no longer seeking co-founders",
                entity_type='project',
                entity_id=project_id,
                metadata={
                    'project_id': project_id,
                    'project_title': project_title,
                    'reason': 'project_stopped_seeking',
                    'swipe_id': swipe['id']
                }
            )
            
            rejected_count += 1
            log_info(f"Auto-rejected swipe {swipe['id']} for project {project_id}")
            
        except Exception as e:
            log_error(f"Failed to notify swiper {swipe.get('swiper_id')} about auto-rejected swipe", error=e)
    
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
