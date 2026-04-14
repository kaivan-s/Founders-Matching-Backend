"""
Slack Integration Service
Handles OAuth, channel creation, and messaging for workspace integrations
"""
import os
import requests
from typing import Optional, Dict, Any, List
from utils.logger import log_info, log_error
from config.database import get_supabase

SLACK_CLIENT_ID = os.getenv('SLACK_CLIENT_ID')
SLACK_CLIENT_SECRET = os.getenv('SLACK_CLIENT_SECRET')
SLACK_REDIRECT_URI = os.getenv('SLACK_REDIRECT_URI', 'https://guild-space.co/api/integrations/slack/callback')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://guild-space.co')

SLACK_OAUTH_URL = 'https://slack.com/api/oauth.v2.access'
SLACK_API_BASE = 'https://slack.com/api'


def get_oauth_url(workspace_id: str, state: str) -> str:
    """Generate Slack OAuth URL for connecting a workspace"""
    scopes = [
        'channels:manage',
        'channels:read', 
        'chat:write',
        'users:read',
        'groups:write',
        'groups:read',
    ]
    
    return (
        f"https://slack.com/oauth/v2/authorize?"
        f"client_id={SLACK_CLIENT_ID}&"
        f"scope={','.join(scopes)}&"
        f"redirect_uri={SLACK_REDIRECT_URI}&"
        f"state={state}"
    )


def exchange_code_for_token(code: str) -> Optional[Dict[str, Any]]:
    """Exchange OAuth code for access token"""
    try:
        response = requests.post(SLACK_OAUTH_URL, data={
            'client_id': SLACK_CLIENT_ID,
            'client_secret': SLACK_CLIENT_SECRET,
            'code': code,
            'redirect_uri': SLACK_REDIRECT_URI,
        })
        
        data = response.json()
        
        if not data.get('ok'):
            log_error(f"Slack OAuth error: {data.get('error')}")
            return None
        
        return {
            'access_token': data.get('access_token'),
            'team_id': data.get('team', {}).get('id'),
            'team_name': data.get('team', {}).get('name'),
            'bot_user_id': data.get('bot_user_id'),
            'authed_user_id': data.get('authed_user', {}).get('id'),
        }
    except Exception as e:
        log_error(f"Error exchanging Slack code: {e}")
        return None


def save_workspace_slack_integration(
    workspace_id: str,
    slack_data: Dict[str, Any],
    connected_by_user_id: str
) -> Dict[str, Any]:
    """Save Slack integration details for a workspace"""
    supabase = get_supabase()
    
    integration_data = {
        'workspace_id': workspace_id,
        'provider': 'slack',
        'access_token': slack_data['access_token'],
        'team_id': slack_data['team_id'],
        'team_name': slack_data['team_name'],
        'bot_user_id': slack_data.get('bot_user_id'),
        'connected_by_user_id': connected_by_user_id,
        'settings': {
            'notifications': {
                'checkin_reminders': True,
                'equity_updates': True,
                'advisor_activity': True,
            }
        },
        'is_active': True,
    }
    
    # Upsert - update if exists, insert if not
    existing = supabase.table('workspace_integrations').select('id').eq(
        'workspace_id', workspace_id
    ).eq('provider', 'slack').execute()
    
    if existing.data:
        result = supabase.table('workspace_integrations').update(
            integration_data
        ).eq('id', existing.data[0]['id']).execute()
    else:
        result = supabase.table('workspace_integrations').insert(
            integration_data
        ).execute()
    
    if not result.data:
        raise ValueError("Failed to save Slack integration")
    
    log_info(f"Slack integration saved for workspace {workspace_id}")
    return result.data[0]


def get_workspace_slack_integration(workspace_id: str) -> Optional[Dict[str, Any]]:
    """Get Slack integration for a workspace"""
    supabase = get_supabase()
    
    result = supabase.table('workspace_integrations').select(
        'id, team_id, team_name, channel_id, channel_name, settings, is_active, created_at'
    ).eq('workspace_id', workspace_id).eq('provider', 'slack').eq('is_active', True).execute()
    
    if not result.data:
        return None
    
    return result.data[0]


def get_workspace_slack_token(workspace_id: str) -> Optional[str]:
    """Get Slack access token for a workspace"""
    supabase = get_supabase()
    
    result = supabase.table('workspace_integrations').select(
        'access_token'
    ).eq('workspace_id', workspace_id).eq('provider', 'slack').eq('is_active', True).execute()
    
    if not result.data:
        return None
    
    return result.data[0].get('access_token')


def create_partnership_channel(
    workspace_id: str,
    channel_name: str,
    founder_emails: List[str] = None
) -> Optional[Dict[str, Any]]:
    """Create a private Slack channel for the partnership"""
    access_token = get_workspace_slack_token(workspace_id)
    if not access_token:
        log_error(f"No Slack token for workspace {workspace_id}")
        return None
    
    # Default channel name if None
    if not channel_name:
        channel_name = 'partnership'
    
    # Sanitize channel name (lowercase, no spaces, max 80 chars)
    safe_name = channel_name.lower().replace(' ', '-').replace('_', '-')
    safe_name = ''.join(c for c in safe_name if c.isalnum() or c == '-')
    safe_name = f"gs-{safe_name}"[:80]
    
    try:
        # Create private channel
        response = requests.post(
            f"{SLACK_API_BASE}/conversations.create",
            headers={'Authorization': f'Bearer {access_token}'},
            json={
                'name': safe_name,
                'is_private': True,
            }
        )
        
        data = response.json()
        
        if not data.get('ok'):
            # Channel might already exist
            if data.get('error') == 'name_taken':
                # Try to find existing channel
                return _find_existing_channel(access_token, safe_name)
            log_error(f"Error creating Slack channel: {data.get('error')}")
            return None
        
        channel = data.get('channel', {})
        channel_id = channel.get('id')
        
        # Save channel info to integration
        supabase = get_supabase()
        supabase.table('workspace_integrations').update({
            'channel_id': channel_id,
            'channel_name': safe_name,
        }).eq('workspace_id', workspace_id).eq('provider', 'slack').execute()
        
        # Post welcome message
        _post_welcome_message(access_token, channel_id, workspace_id)
        
        log_info(f"Created Slack channel {safe_name} for workspace {workspace_id}")
        
        return {
            'channel_id': channel_id,
            'channel_name': safe_name,
        }
        
    except Exception as e:
        log_error(f"Error creating Slack channel: {e}")
        return None


def _find_existing_channel(access_token: str, channel_name: str) -> Optional[Dict[str, Any]]:
    """Find an existing channel by name"""
    try:
        response = requests.get(
            f"{SLACK_API_BASE}/conversations.list",
            headers={'Authorization': f'Bearer {access_token}'},
            params={'types': 'private_channel', 'limit': 1000}
        )
        
        data = response.json()
        if data.get('ok'):
            for channel in data.get('channels', []):
                if channel.get('name') == channel_name:
                    return {
                        'channel_id': channel.get('id'),
                        'channel_name': channel_name,
                    }
        return None
    except Exception as e:
        log_error(f"Error finding Slack channel: {e}")
        return None


def _post_welcome_message(access_token: str, channel_id: str, workspace_id: str):
    """Post a welcome message to the new channel"""
    try:
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🎉 Welcome to your Guild Space partnership channel!",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "This channel is connected to your Guild Space workspace. You'll receive notifications here for:\n\n• 📝 Weekly check-in reminders\n• ⚖️ Equity agreement updates\n• 👥 Advisor activity"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{FRONTEND_URL}/workspaces/{workspace_id}|Open Guild Space Workspace>"
                }
            }
        ]
        
        requests.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers={'Authorization': f'Bearer {access_token}'},
            json={
                'channel': channel_id,
                'blocks': blocks,
                'text': 'Welcome to your Guild Space partnership channel!',
            }
        )
    except Exception as e:
        log_error(f"Error posting welcome message: {e}")


def send_slack_notification(
    workspace_id: str,
    message: str,
    blocks: List[Dict] = None,
    notification_type: str = 'general'
) -> bool:
    """Send a notification to the workspace's Slack channel"""
    integration = get_workspace_slack_integration(workspace_id)
    if not integration or not integration.get('channel_id'):
        return False
    
    # Check if this notification type is enabled
    settings = integration.get('settings', {})
    notifications = settings.get('notifications', {})
    
    type_mapping = {
        'checkin_reminder': 'checkin_reminders',
        'checkin_submitted': 'checkin_reminders',
        'equity_update': 'equity_updates',
        'equity_approved': 'equity_updates',
        'advisor_joined': 'advisor_activity',
        'advisor_update': 'advisor_activity',
    }
    
    setting_key = type_mapping.get(notification_type, None)
    if setting_key and not notifications.get(setting_key, True):
        return False  # Notification type disabled
    
    access_token = get_workspace_slack_token(workspace_id)
    if not access_token:
        return False
    
    try:
        payload = {
            'channel': integration['channel_id'],
            'text': message,
        }
        
        if blocks:
            payload['blocks'] = blocks
        
        response = requests.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers={'Authorization': f'Bearer {access_token}'},
            json=payload
        )
        
        data = response.json()
        if not data.get('ok'):
            log_error(f"Error sending Slack message: {data.get('error')}")
            return False
        
        return True
        
    except Exception as e:
        log_error(f"Error sending Slack notification: {e}")
        return False


def send_checkin_reminder(workspace_id: str, workspace_title: str) -> bool:
    """Send weekly check-in reminder to Slack"""
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"📝 *Time for your weekly check-in!*\n\nTake 2 minutes to share your progress on _{workspace_title}_."
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Submit Check-in",
                        "emoji": True
                    },
                    "url": f"{FRONTEND_URL}/workspaces/{workspace_id}/overview",
                    "style": "primary"
                }
            ]
        }
    ]
    
    return send_slack_notification(
        workspace_id,
        "Time for your weekly check-in!",
        blocks=blocks,
        notification_type='checkin_reminder'
    )


def send_checkin_submitted_notification(
    workspace_id: str,
    founder_name: str,
    health_status: str
) -> bool:
    """Notify when a founder submits their check-in"""
    emoji_map = {
        'on_track': '🟢',
        'needs_attention': '🟡', 
        'off_track': '🔴',
    }
    emoji = emoji_map.get(health_status, '📝')
    
    message = f"{emoji} *{founder_name}* submitted their weekly check-in"
    
    return send_slack_notification(
        workspace_id,
        message,
        notification_type='checkin_submitted'
    )


def send_equity_notification(
    workspace_id: str,
    event_type: str,
    details: Dict[str, Any]
) -> bool:
    """Send equity-related notifications"""
    if event_type == 'scenario_created':
        message = f"⚖️ New equity scenario created: {details.get('founder_a_percent')}% / {details.get('founder_b_percent')}%"
    elif event_type == 'scenario_approved':
        message = f"✅ *{details.get('founder_name')}* approved the equity split"
    elif event_type == 'both_approved':
        message = "🎉 *Both founders approved!* Your equity agreement is ready."
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Download Agreement",
                            "emoji": True
                        },
                        "url": f"{FRONTEND_URL}/workspaces/{workspace_id}/equity-roles",
                        "style": "primary"
                    }
                ]
            }
        ]
        return send_slack_notification(workspace_id, message, blocks=blocks, notification_type='equity_update')
    elif event_type == 'document_generated':
        message = "📄 Co-founder agreement generated and ready for download"
    else:
        return False
    
    return send_slack_notification(workspace_id, message, notification_type='equity_update')


def send_advisor_notification(
    workspace_id: str,
    event_type: str,
    advisor_name: str
) -> bool:
    """Send advisor-related notifications"""
    if event_type == 'advisor_joined':
        message = f"👋 *{advisor_name}* joined as your advisor!"
    elif event_type == 'advisor_left':
        message = f"👋 *{advisor_name}* is no longer your advisor"
    else:
        return False
    
    return send_slack_notification(workspace_id, message, notification_type='advisor_joined')


def update_notification_settings(
    workspace_id: str,
    settings: Dict[str, bool]
) -> bool:
    """Update Slack notification settings for a workspace"""
    supabase = get_supabase()
    
    try:
        result = supabase.table('workspace_integrations').select(
            'id, settings'
        ).eq('workspace_id', workspace_id).eq('provider', 'slack').execute()
        
        if not result.data:
            return False
        
        current_settings = result.data[0].get('settings', {})
        current_settings['notifications'] = settings
        
        supabase.table('workspace_integrations').update({
            'settings': current_settings
        }).eq('id', result.data[0]['id']).execute()
        
        return True
    except Exception as e:
        log_error(f"Error updating Slack settings: {e}")
        return False


def disconnect_slack(workspace_id: str) -> bool:
    """Disconnect Slack integration for a workspace"""
    supabase = get_supabase()
    
    try:
        supabase.table('workspace_integrations').update({
            'is_active': False,
            'access_token': None,
        }).eq('workspace_id', workspace_id).eq('provider', 'slack').execute()
        
        log_info(f"Slack disconnected for workspace {workspace_id}")
        return True
    except Exception as e:
        log_error(f"Error disconnecting Slack: {e}")
        return False
