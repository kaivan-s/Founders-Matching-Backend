"""Feed service for advisor-founder collaboration - activity feed, meetings, check-ins"""
from config.database import get_supabase
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


def _get_founder_id(clerk_user_id: str) -> str:
    """Get founder ID from clerk user ID"""
    supabase = get_supabase()
    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        raise ValueError("User not found")
    return founder.data[0]['id']


def _verify_workspace_access(clerk_user_id: str, workspace_id: str) -> tuple:
    """Verify user has access to workspace and return (founder_id, role)"""
    supabase = get_supabase()
    founder_id = _get_founder_id(clerk_user_id)
    
    participant = supabase.table('workspace_participants').select('id, role').eq(
        'workspace_id', workspace_id
    ).eq('user_id', founder_id).execute()
    
    if not participant.data:
        raise ValueError("Access denied to this workspace")
    
    role = participant.data[0].get('role', 'FOUNDER').lower()
    if role not in ['founder', 'advisor']:
        role = 'founder'
    
    return founder_id, role


# ============================================
# ACTIVITY FEED
# ============================================

def get_feed_posts(clerk_user_id: str, workspace_id: str, limit: int = 50, offset: int = 0) -> List[Dict]:
    """Get activity feed posts for a workspace"""
    founder_id, _ = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    result = supabase.table('workspace_feed_posts').select(
        '*, author:founders!author_id(id, name, profile_picture)'
    ).eq('workspace_id', workspace_id).order(
        'created_at', desc=True
    ).range(offset, offset + limit - 1).execute()
    
    posts = result.data if result.data else []
    
    # Fetch replies for each post
    if posts:
        post_ids = [p['id'] for p in posts]
        replies = supabase.table('workspace_feed_replies').select(
            '*, author:founders!author_id(id, name, profile_picture)'
        ).in_('post_id', post_ids).order('created_at', desc=False).execute()
        
        replies_by_post = {}
        for reply in (replies.data or []):
            pid = reply['post_id']
            if pid not in replies_by_post:
                replies_by_post[pid] = []
            replies_by_post[pid].append(reply)
        
        for post in posts:
            post['replies'] = replies_by_post.get(post['id'], [])
    
    return posts


def create_feed_post(clerk_user_id: str, workspace_id: str, content: str, 
                     post_type: str = 'message', metadata: Optional[Dict] = None) -> Dict:
    """Create a new feed post"""
    founder_id, role = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    if not content or not content.strip():
        raise ValueError("Content is required")
    
    if post_type not in ['message', 'meeting_note', 'system']:
        post_type = 'message'
    
    post_data = {
        'workspace_id': workspace_id,
        'author_id': founder_id,
        'author_role': role,
        'post_type': post_type,
        'content': content.strip(),
        'metadata': metadata or {},
    }
    
    result = supabase.table('workspace_feed_posts').insert(post_data).execute()
    if not result.data:
        raise ValueError("Failed to create post")
    
    # Fetch with author info
    post_id = result.data[0]['id']
    post = supabase.table('workspace_feed_posts').select(
        '*, author:founders!author_id(id, name, profile_picture)'
    ).eq('id', post_id).execute()
    
    post_data = post.data[0] if post.data else result.data[0]
    post_data['replies'] = []
    
    # Create notification for other participants
    _create_feed_notification(workspace_id, founder_id, post_data)
    
    return post_data


def create_feed_reply(clerk_user_id: str, workspace_id: str, post_id: str, content: str) -> Dict:
    """Create a reply to a feed post"""
    founder_id, role = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    if not content or not content.strip():
        raise ValueError("Content is required")
    
    # Verify post exists and belongs to workspace
    post = supabase.table('workspace_feed_posts').select('id, author_id').eq(
        'id', post_id
    ).eq('workspace_id', workspace_id).execute()
    
    if not post.data:
        raise ValueError("Post not found")
    
    reply_data = {
        'post_id': post_id,
        'author_id': founder_id,
        'author_role': role,
        'content': content.strip(),
    }
    
    result = supabase.table('workspace_feed_replies').insert(reply_data).execute()
    if not result.data:
        raise ValueError("Failed to create reply")
    
    # Fetch with author info
    reply_id = result.data[0]['id']
    reply = supabase.table('workspace_feed_replies').select(
        '*, author:founders!author_id(id, name, profile_picture)'
    ).eq('id', reply_id).execute()
    
    return reply.data[0] if reply.data else result.data[0]


def delete_feed_post(clerk_user_id: str, workspace_id: str, post_id: str) -> None:
    """Delete a feed post (only author can delete)"""
    founder_id, _ = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    post = supabase.table('workspace_feed_posts').select('id, author_id').eq(
        'id', post_id
    ).eq('workspace_id', workspace_id).execute()
    
    if not post.data:
        raise ValueError("Post not found")
    
    if post.data[0]['author_id'] != founder_id:
        raise ValueError("You can only delete your own posts")
    
    supabase.table('workspace_feed_posts').delete().eq('id', post_id).execute()


# ============================================
# MEETINGS
# ============================================

def get_meetings(clerk_user_id: str, workspace_id: str, limit: int = 20) -> List[Dict]:
    """Get meetings for a workspace"""
    founder_id, _ = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    result = supabase.table('workspace_meetings').select(
        '*, logged_by_user:founders!logged_by(id, name)'
    ).eq('workspace_id', workspace_id).order('meeting_date', desc=True).limit(limit).execute()
    
    meetings = result.data if result.data else []
    
    # Resolve attendee names
    if meetings:
        all_attendee_ids = set()
        for m in meetings:
            all_attendee_ids.update(m.get('attendees', []))
        
        if all_attendee_ids:
            attendees = supabase.table('founders').select('id, name').in_(
                'id', list(all_attendee_ids)
            ).execute()
            attendee_map = {a['id']: a['name'] for a in (attendees.data or [])}
            
            for m in meetings:
                m['attendee_names'] = [attendee_map.get(aid, 'Unknown') for aid in m.get('attendees', [])]
    
    return meetings


def create_meeting(clerk_user_id: str, workspace_id: str, data: Dict) -> Dict:
    """Log a new meeting"""
    founder_id, role = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    if not data.get('meeting_date'):
        raise ValueError("Meeting date is required")
    if not data.get('duration_minutes') or data['duration_minutes'] <= 0:
        raise ValueError("Duration must be greater than 0")
    if not data.get('attendees') or len(data['attendees']) == 0:
        raise ValueError("At least one attendee is required")
    
    meeting_data = {
        'workspace_id': workspace_id,
        'logged_by': founder_id,
        'meeting_date': data['meeting_date'],
        'duration_minutes': data['duration_minutes'],
        'attendees': data['attendees'],
        'summary': data.get('summary', '').strip() or None,
        'action_items': data.get('action_items', '').strip() or None,
    }
    
    result = supabase.table('workspace_meetings').insert(meeting_data).execute()
    if not result.data:
        raise ValueError("Failed to create meeting")
    
    meeting = result.data[0]
    
    # Create feed post for the meeting
    duration_hours = meeting_data['duration_minutes'] // 60
    duration_mins = meeting_data['duration_minutes'] % 60
    duration_str = f"{duration_hours}h {duration_mins}m" if duration_hours else f"{duration_mins}m"
    
    feed_content = f"📅 Logged a meeting ({duration_str})"
    if meeting_data['summary']:
        feed_content += f"\n\n{meeting_data['summary']}"
    
    feed_post = create_feed_post(
        clerk_user_id, workspace_id, feed_content, 
        post_type='meeting_note',
        metadata={'meeting_id': meeting['id']}
    )
    
    # Link feed post to meeting
    supabase.table('workspace_meetings').update({
        'feed_post_id': feed_post['id']
    }).eq('id', meeting['id']).execute()
    
    meeting['feed_post_id'] = feed_post['id']
    
    return meeting


# ============================================
# CHECK-INS
# ============================================

def get_checkin_status(clerk_user_id: str, workspace_id: str) -> Dict:
    """Check if user needs to complete a check-in this month"""
    founder_id, role = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    now = datetime.now(timezone.utc)
    current_month = now.month
    current_year = now.year
    
    # Check if already completed for this month
    existing = supabase.table('advisor_engagement_checkins').select('id, created_at').eq(
        'workspace_id', workspace_id
    ).eq('respondent_id', founder_id).eq(
        'period_month', current_month
    ).eq('period_year', current_year).execute()
    
    if existing.data:
        return {
            'checkin_due': False,
            'already_completed': True,
            'completed_at': existing.data[0]['created_at'],
            'period_month': current_month,
            'period_year': current_year,
        }
    
    # Check if workspace has an advisor (check-ins only relevant if advisor exists)
    advisor = supabase.table('workspace_participants').select('id, joined_at').eq(
        'workspace_id', workspace_id
    ).eq('role', 'ADVISOR').execute()
    
    if not advisor.data:
        return {
            'checkin_due': False,
            'no_advisor': True,
            'period_month': current_month,
            'period_year': current_year,
        }
    
    # Check if enough time has passed (at least 30 days since advisor joined or last check-in)
    advisor_joined = advisor.data[0].get('joined_at')
    if advisor_joined:
        joined_date = datetime.fromisoformat(advisor_joined.replace('Z', '+00:00'))
        days_since_joined = (now - joined_date).days
        if days_since_joined < 30:
            return {
                'checkin_due': False,
                'too_early': True,
                'days_until_due': 30 - days_since_joined,
                'period_month': current_month,
                'period_year': current_year,
            }
    
    return {
        'checkin_due': True,
        'period_month': current_month,
        'period_year': current_year,
    }


def get_checkins(clerk_user_id: str, workspace_id: str) -> List[Dict]:
    """Get all check-ins for a workspace"""
    founder_id, _ = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    result = supabase.table('advisor_engagement_checkins').select(
        '*, respondent:founders!respondent_id(id, name)'
    ).eq('workspace_id', workspace_id).order('period_year', desc=True).order(
        'period_month', desc=True
    ).execute()
    
    return result.data if result.data else []


def create_checkin(clerk_user_id: str, workspace_id: str, data: Dict) -> Dict:
    """Submit a monthly check-in"""
    founder_id, role = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    now = datetime.now(timezone.utc)
    current_month = now.month
    current_year = now.year
    
    if not data.get('rating') or not (1 <= data['rating'] <= 5):
        raise ValueError("Rating must be between 1 and 5")
    if data.get('meeting_expectations') not in ['yes', 'partially', 'no']:
        raise ValueError("meeting_expectations must be yes, partially, or no")
    
    checkin_data = {
        'workspace_id': workspace_id,
        'respondent_id': founder_id,
        'respondent_role': role,
        'rating': data['rating'],
        'meeting_expectations': data['meeting_expectations'],
        'comment': data.get('comment', '').strip() or None,
        'period_month': current_month,
        'period_year': current_year,
    }
    
    # Upsert to handle duplicate submissions
    result = supabase.table('advisor_engagement_checkins').upsert(
        checkin_data,
        on_conflict='workspace_id,respondent_id,period_month,period_year'
    ).execute()
    
    if not result.data:
        raise ValueError("Failed to submit check-in")
    
    return result.data[0]


# ============================================
# ACTIVITY LOGS (Hours tracking)
# ============================================

def get_activity_logs(clerk_user_id: str, workspace_id: str, limit: int = 30) -> List[Dict]:
    """Get activity logs for a workspace"""
    founder_id, _ = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    result = supabase.table('advisor_activity_logs').select(
        '*, advisor:founders!advisor_id(id, name)'
    ).eq('workspace_id', workspace_id).order('log_date', desc=True).limit(limit).execute()
    
    return result.data if result.data else []


def create_activity_log(clerk_user_id: str, workspace_id: str, data: Dict) -> Dict:
    """Log advisor activity/hours"""
    founder_id, role = _verify_workspace_access(clerk_user_id, workspace_id)
    
    # Only advisors can log activity
    if role != 'advisor':
        raise ValueError("Only advisors can log activity hours")
    
    supabase = get_supabase()
    
    if not data.get('log_date'):
        raise ValueError("Date is required")
    if not data.get('hours') or data['hours'] <= 0:
        raise ValueError("Hours must be greater than 0")
    if data['hours'] > 24:
        raise ValueError("Hours cannot exceed 24")
    
    log_data = {
        'workspace_id': workspace_id,
        'advisor_id': founder_id,
        'log_date': data['log_date'],
        'hours': data['hours'],
        'notes': data.get('notes', '').strip() or None,
    }
    
    result = supabase.table('advisor_activity_logs').insert(log_data).execute()
    if not result.data:
        raise ValueError("Failed to create activity log")
    
    return result.data[0]


def get_activity_summary(clerk_user_id: str, workspace_id: str) -> Dict:
    """Get summary of advisor activity for a workspace"""
    founder_id, _ = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    # Get all activity logs
    logs = supabase.table('advisor_activity_logs').select('hours, log_date').eq(
        'workspace_id', workspace_id
    ).execute()
    
    total_hours = sum(float(log['hours']) for log in (logs.data or []))
    
    # Get meeting count
    meetings = supabase.table('workspace_meetings').select('id').eq(
        'workspace_id', workspace_id
    ).execute()
    
    # Get feed post count
    posts = supabase.table('workspace_feed_posts').select('id').eq(
        'workspace_id', workspace_id
    ).execute()
    
    # Get latest check-ins
    checkins = supabase.table('advisor_engagement_checkins').select(
        'rating, respondent_role, period_month, period_year'
    ).eq('workspace_id', workspace_id).order(
        'period_year', desc=True
    ).order('period_month', desc=True).limit(4).execute()
    
    return {
        'total_hours_logged': round(total_hours, 1),
        'total_meetings': len(meetings.data or []),
        'total_posts': len(posts.data or []),
        'recent_checkins': checkins.data or [],
    }


# ============================================
# NOTIFICATIONS HELPER
# ============================================

def _create_feed_notification(workspace_id: str, author_id: str, post: Dict) -> None:
    """Create notifications for other workspace participants"""
    supabase = get_supabase()
    
    # Get all participants except author
    participants = supabase.table('workspace_participants').select('user_id').eq(
        'workspace_id', workspace_id
    ).neq('user_id', author_id).execute()
    
    if not participants.data:
        return
    
    author_name = post.get('author', {}).get('name', 'Someone')
    post_type = post.get('post_type', 'message')
    
    if post_type == 'meeting_note':
        title = f"{author_name} logged a meeting"
    else:
        title = f"{author_name} posted an update"
    
    for p in participants.data:
        try:
            supabase.table('notifications').insert({
                'user_id': p['user_id'],
                'type': 'feed_post',
                'title': title,
                'message': post.get('content', '')[:100] + ('...' if len(post.get('content', '')) > 100 else ''),
                'data': {
                    'workspace_id': workspace_id,
                    'post_id': post.get('id'),
                    'post_type': post_type,
                }
            }).execute()
        except Exception:
            # Don't fail if notification creation fails
            pass


# ============================================
# WORKSPACE PARTICIPANTS HELPER
# ============================================

def get_workspace_participants_with_roles(clerk_user_id: str, workspace_id: str) -> List[Dict]:
    """Get all participants in a workspace with their roles (for attendee selection, etc.)"""
    founder_id, _ = _verify_workspace_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    
    result = supabase.table('workspace_participants').select(
        'user_id, role, joined_at, founders!workspace_participants_user_id_fkey(id, name, profile_picture)'
    ).eq('workspace_id', workspace_id).execute()
    
    participants = []
    for p in (result.data or []):
        founder_info = p.get('founders', {})
        participants.append({
            'id': p['user_id'],
            'name': founder_info.get('name', 'Unknown'),
            'profile_picture': founder_info.get('profile_picture'),
            'role': p.get('role', 'FOUNDER'),
            'joined_at': p.get('joined_at'),
        })
    
    return participants
