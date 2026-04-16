"""Swipe-related business logic"""
import traceback
import threading
from config.database import get_supabase
from services import plan_service, email_service


def _send_interest_email_async(swiper_name, owner_email, owner_name, project_title, swipe_id):
    """Send interest email in background thread"""
    try:
        email_service.send_interest_received_email(
            to_email=owner_email,
            user_name=owner_name or 'there',
            interested_user_name=swiper_name or 'Someone',
            project_name=project_title or 'your project'
        )
    except Exception as e:
        from utils.logger import log_error
        log_error(f"Failed to send interest notification email for swipe {swipe_id}", error=e)


def create_swipe(clerk_user_id, data):
    """Record a swipe action - uses plan-based limits instead of credits
    Now supports project-based swiping where users swipe on specific projects
    Limits are enforced via plan_service.check_discovery_limit()
    
    OPTIMIZED: Reduced from ~15 DB calls to ~6 by caching founder_id and plan
    """
    supabase = get_supabase()
    
    swiped_id = data.get('swiped_id')
    swipe_type = data.get('swipe_type')
    project_id = data.get('project_id')
    
    if not swiped_id or not swipe_type:
        raise ValueError("swiped_id and swipe_type required")
    
    if not project_id:
        raise ValueError("project_id is required. All swipes must be project-based.")
    
    # OPTIMIZATION: Single query to get swiper profile WITH plan info
    user_profile = supabase.table('founders').select(
        'id, name, plan, subscription_status, subscription_current_period_end'
    ).eq('clerk_user_id', clerk_user_id).execute()
    
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    swiper_data = user_profile.data[0]
    swiper_id = swiper_data['id']
    swiper_name = swiper_data.get('name')
    user_plan = swiper_data.get('plan', 'FREE')
    
    # OPTIMIZATION: Check if user has unlimited plan (skip limit checks entirely)
    is_unlimited = user_plan in ('PRO', 'PRO_PLUS')
    
    # Validate subscription is still active for paid plans
    if is_unlimited:
        subscription_status = swiper_data.get('subscription_status')
        if subscription_status in ('canceled', 'expired', 'past_due', 'unpaid'):
            is_unlimited = False
    
    # OPTIMIZATION: Combined query for project validation AND owner info (for email)
    project_check = supabase.table('projects').select(
        'founder_id, seeking_cofounder, is_active, title, founders!inner(name, email)'
    ).eq('id', project_id).execute()
    
    if not project_check.data:
        raise ValueError("Project not found")
    
    project_data = project_check.data[0]
    if project_data['founder_id'] != swiped_id:
        raise ValueError("Project does not belong to the specified founder")
    if not project_data['is_active']:
        raise ValueError("Project is not active")
    if not project_data['seeking_cofounder']:
        raise ValueError("Project is no longer seeking a co-founder")
    
    # Cache owner info for email (avoid extra queries later)
    owner_info = project_data.get('founders', {})
    project_title = project_data.get('title')
    
    # Check if swipe already exists FIRST
    existing_swipe = supabase.table('swipes').select('id').eq(
        'swiper_id', swiper_id
    ).eq('swiped_id', swiped_id).eq('project_id', project_id).execute()
    
    if existing_swipe.data:
        return existing_swipe.data[0]
    
    # For right swipes, check limits (skip for unlimited plans)
    if swipe_type == 'right' and not is_unlimited:
        # Check workspace limit
        can_create_workspace, current_workspace_count, max_workspaces = plan_service.check_workspace_limit(clerk_user_id)
        if not can_create_workspace:
            raise ValueError(f"Workspace limit reached. You currently have {current_workspace_count} of {max_workspaces} workspaces. Please upgrade your plan or remove existing workspaces before sending new connection requests.")
        
        # Check discovery limit
        can_swipe, current_count, max_allowed = plan_service.check_discovery_limit(clerk_user_id)
        if not can_swipe:
            raise ValueError(f"Discovery limit reached. You've used {current_count} of {max_allowed} swipes this month. Upgrade to Pro for unlimited discovery.")
    
    # Insert swipe
    swipe_data = {
        'swiper_id': swiper_id,
        'swiped_id': swiped_id,
        'swipe_type': swipe_type,
        'project_id': project_id
    }
    
    try:
        response = supabase.table('swipes').insert(swipe_data).execute()
    except Exception as e:
        if 'duplicate key value' in str(e):
            existing = supabase.table('swipes').select('*').eq(
                'swiper_id', swiper_id
            ).eq('swiped_id', swiped_id).eq('project_id', project_id).execute()
            if existing.data:
                return existing.data[0]
        raise e
    
    result = response.data[0]
    result['match_created'] = False
    
    # For right swipes: increment usage and send notification
    if swipe_type == 'right':
        # Increment usage (only for FREE plan users - others are unlimited)
        if not is_unlimited:
            try:
                plan_service.increment_discovery_usage(clerk_user_id)
                plan_service.record_swipe_in_history(clerk_user_id, swipe_type, project_id)
            except Exception as e:
                from utils.logger import log_error
                log_error(f"Failed to increment discovery usage for swipe {result.get('id')}", error=e)
        
        # OPTIMIZATION: Send email in background thread (non-blocking)
        if owner_info.get('email'):
            email_thread = threading.Thread(
                target=_send_interest_email_async,
                args=(swiper_name, owner_info.get('email'), owner_info.get('name'), project_title, result.get('id'))
            )
            email_thread.daemon = True
            email_thread.start()
    
    return result

