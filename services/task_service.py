"""Task-related business logic for Kanban-style task board"""
from config.database import get_supabase
from datetime import datetime
from typing import Optional, List, Dict, Any

def _verify_workspace_access(clerk_user_id: str, workspace_id: str) -> str:
    """Verify user has access to workspace and return founder_id"""
    supabase = get_supabase()
    
    # Get founder ID
    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        raise ValueError("Founder not found")
    founder_id = founder.data[0]['id']
    
    # Verify workspace access
    participant = supabase.table('workspace_participants').select('id').eq('workspace_id', workspace_id).eq('user_id', founder_id).execute()
    if not participant.data:
        raise ValueError("Access denied")
    
    return founder_id

def get_tasks(clerk_user_id: str, workspace_id: str, owner_filter: Optional[str] = None, link_filter: Optional[str] = None) -> List[Dict]:
    """Get all tasks for a workspace with optional filters"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    query = supabase.table('workspace_tasks').select(
        '*, owner:founders!owner_id(id, name), kpi:workspace_kpis(id, label), decision:workspace_decisions(id, content)'
    ).eq('workspace_id', workspace_id)
    
    # Apply owner filter
    if owner_filter == 'me':
        query = query.eq('owner_id', founder_id)
    elif owner_filter == 'other':
        # Get other founder's ID
        participants = supabase.table('workspace_participants').select('user_id').eq('workspace_id', workspace_id).neq('user_id', founder_id).execute()
        if participants.data:
            other_founder_id = participants.data[0]['user_id']
            query = query.eq('owner_id', other_founder_id)
    
    # Apply link filter
    if link_filter == 'kpi':
        query = query.not_.is_('kpi_id', 'null')
    elif link_filter == 'decision':
        query = query.not_.is_('decision_id', 'null')
    
    result = query.order('created_at', desc=False).execute()
    return result.data if result.data else []

def create_task(clerk_user_id: str, workspace_id: str, data: Dict) -> Dict:
    """Create a new task"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Validate that at least one link is provided
    if not data.get('kpi_id') and not data.get('decision_id'):
        raise ValueError("Task must be linked to either a KPI or Decision")
    
    # Validate that only one link is provided
    if data.get('kpi_id') and data.get('decision_id'):
        raise ValueError("Task can only be linked to either a KPI or Decision, not both")
    
    task_data = {
        'workspace_id': workspace_id,
        'title': data['title'],
        'owner_id': data.get('owner_id', founder_id),  # Default to current user
        'status': data.get('status', 'TODO'),
        'due_date': data.get('due_date'),
        'kpi_id': data.get('kpi_id'),
        'decision_id': data.get('decision_id'),
    }
    
    result = supabase.table('workspace_tasks').insert(task_data).execute()
    if not result.data:
        raise ValueError("Failed to create task")
    
    # Fetch with relations
    task_id = result.data[0]['id']
    task = supabase.table('workspace_tasks').select(
        '*, owner:founders!owner_id(id, name), kpi:workspace_kpis(id, label), decision:workspace_decisions(id, content)'
    ).eq('id', task_id).execute()
    
    return task.data[0] if task.data else result.data[0]

def update_task(clerk_user_id: str, workspace_id: str, task_id: str, data: Dict) -> Dict:
    """Update a task"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Verify task exists and belongs to workspace
    task = supabase.table('workspace_tasks').select('*').eq('id', task_id).eq('workspace_id', workspace_id).execute()
    if not task.data:
        raise ValueError("Task not found")
    
    update_data = {}
    if 'title' in data:
        update_data['title'] = data['title']
    if 'owner_id' in data:
        update_data['owner_id'] = data['owner_id']
    if 'status' in data:
        update_data['status'] = data['status']
        # If transitioning to DONE, completed_at will be set by trigger
    if 'due_date' in data:
        update_data['due_date'] = data['due_date']
    
    result = supabase.table('workspace_tasks').update(update_data).eq('id', task_id).execute()
    if not result.data:
        raise ValueError("Failed to update task")
    
    # Fetch with relations
    updated_task = supabase.table('workspace_tasks').select(
        '*, owner:founders!owner_id(id, name), kpi:workspace_kpis(id, label), decision:workspace_decisions(id, content)'
    ).eq('id', task_id).execute()
    
    return updated_task.data[0] if updated_task.data else result.data[0]

def delete_task(clerk_user_id: str, workspace_id: str, task_id: str) -> None:
    """Delete a task"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Verify task exists and belongs to workspace
    task = supabase.table('workspace_tasks').select('id').eq('id', task_id).eq('workspace_id', workspace_id).execute()
    if not task.data:
        raise ValueError("Task not found")
    
    supabase.table('workspace_tasks').delete().eq('id', task_id).execute()

def get_task_metrics(clerk_user_id: str, workspace_id: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> Dict:
    """Get task metrics for investor reporting"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Build date filter
    date_filter = supabase.table('workspace_tasks').select('*').eq('workspace_id', workspace_id).eq('status', 'DONE')
    if from_date:
        date_filter = date_filter.gte('completed_at', from_date)
    if to_date:
        date_filter = date_filter.lte('completed_at', to_date)
    
    completed_tasks = date_filter.execute()
    tasks_data = completed_tasks.data if completed_tasks.data else []
    
    # Tasks done by founder
    tasks_done_by_founder = {}
    for task in tasks_data:
        owner_id = task['owner_id']
        if owner_id not in tasks_done_by_founder:
            tasks_done_by_founder[owner_id] = 0
        tasks_done_by_founder[owner_id] += 1
    
    # Get founder names
    founder_ids = list(tasks_done_by_founder.keys())
    if founder_ids:
        founders = supabase.table('founders').select('id, name').in_('id', founder_ids).execute()
        founder_map = {f['id']: f['name'] for f in founders.data} if founders.data else {}
        tasks_done_by_founder = {
            founder_map.get(fid, 'Unknown'): count 
            for fid, count in tasks_done_by_founder.items()
        }
    
    # Tasks per KPI
    kpi_tasks = {}
    for task in tasks_data:
        if task.get('kpi_id'):
            kpi_id = task['kpi_id']
            if kpi_id not in kpi_tasks:
                kpi_tasks[kpi_id] = 0
            kpi_tasks[kpi_id] += 1
    
    # Get KPI labels
    kpi_list = []
    if kpi_tasks:
        kpi_ids = list(kpi_tasks.keys())
        kpis = supabase.table('workspace_kpis').select('id, label').in_('id', kpi_ids).execute()
        if kpis.data:
            kpi_list = [
                {'kpi_id': kpi['id'], 'kpi_name': kpi['label'], 'task_count': kpi_tasks[kpi['id']]}
                for kpi in kpis.data
            ]
    
    # Tasks per Decision
    decision_tasks = {}
    for task in tasks_data:
        if task.get('decision_id'):
            decision_id = task['decision_id']
            if decision_id not in decision_tasks:
                decision_tasks[decision_id] = 0
            decision_tasks[decision_id] += 1
    
    # Get Decision content previews
    decision_list = []
    if decision_tasks:
        decision_ids = list(decision_tasks.keys())
        decisions = supabase.table('workspace_decisions').select('id, content').in_('id', decision_ids).execute()
        if decisions.data:
            decision_list = [
                {
                    'decision_id': decision['id'],
                    'decision_preview': decision['content'][:100] + ('...' if len(decision['content']) > 100 else ''),
                    'task_count': decision_tasks[decision['id']]
                }
                for decision in decisions.data
            ]
    
    return {
        'tasks_done_by_founder': tasks_done_by_founder,
        'tasks_per_kpi': kpi_list,
        'tasks_per_decision': decision_list,
        'total_completed_tasks': len(tasks_data)
    }

def get_completed_tasks_for_week(clerk_user_id: str, workspace_id: str, week_start: str, week_end: str) -> List[Dict]:
    """Get completed tasks for a specific week (for check-ins)"""
    founder_id = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    tasks = supabase.table('workspace_tasks').select(
        'id, title, owner_id, completed_at, owner:founders!owner_id(name)'
    ).eq('workspace_id', workspace_id).eq('status', 'DONE').gte('completed_at', week_start).lte('completed_at', week_end).order('completed_at', desc=False).execute()
    
    return tasks.data if tasks.data else []

