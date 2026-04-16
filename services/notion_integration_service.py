"""
Notion Integration Service
Handles OAuth, page creation, and database setup for workspace integrations
"""
import os
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime
from utils.logger import log_info, log_error
from config.database import get_supabase

NOTION_CLIENT_ID = (os.getenv('NOTION_CLIENT_ID') or '').strip()
NOTION_CLIENT_SECRET = (os.getenv('NOTION_CLIENT_SECRET') or '').strip()
NOTION_REDIRECT_URI = (os.getenv('NOTION_REDIRECT_URI') or '').strip()
FRONTEND_URL = (os.getenv('FRONTEND_URL') or 'https://guild-space.co').strip()

NOTION_TOKEN_URL = 'https://api.notion.com/v1/oauth/token'
NOTION_API_BASE = 'https://api.notion.com/v1'
NOTION_VERSION = '2022-06-28'


class NotionWorkspaceMismatchError(Exception):
    """Raised when co-founder tries to connect a different Notion workspace"""
    def __init__(self, existing_workspace_name: str, new_workspace_name: str):
        self.existing_workspace_name = existing_workspace_name
        self.new_workspace_name = new_workspace_name
        super().__init__(f"Notion workspace mismatch: existing={existing_workspace_name}, new={new_workspace_name}")


def get_oauth_url(workspace_id: str, state: str) -> str:
    """Generate Notion OAuth URL for connecting a workspace"""
    from urllib.parse import urlencode
    
    params = {
        'client_id': NOTION_CLIENT_ID,
        'response_type': 'code',
        'owner': 'user',
        'redirect_uri': NOTION_REDIRECT_URI,
        'state': state,
    }
    
    return f"https://api.notion.com/v1/oauth/authorize?{urlencode(params)}"


def exchange_code_for_token(code: str) -> Optional[Dict[str, Any]]:
    """Exchange OAuth code for access token"""
    import base64
    
    try:
        if not NOTION_CLIENT_ID or not NOTION_CLIENT_SECRET:
            log_error("Notion OAuth credentials not configured (NOTION_CLIENT_ID or NOTION_CLIENT_SECRET missing)")
            return None
        
        # Notion uses Basic Auth for token exchange
        credentials = base64.b64encode(
            f"{NOTION_CLIENT_ID}:{NOTION_CLIENT_SECRET}".encode()
        ).decode()
        
        log_info(f"Exchanging Notion OAuth code")
        log_info(f"  - redirect_uri: {NOTION_REDIRECT_URI}")
        log_info(f"  - client_id starts with: {NOTION_CLIENT_ID[:8] if NOTION_CLIENT_ID else 'None'}...")
        
        response = requests.post(
            NOTION_TOKEN_URL,
            headers={
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json',
            },
            json={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': NOTION_REDIRECT_URI,
            }
        )
        
        data = response.json()
        log_info(f"Notion OAuth response status: {response.status_code}")
        
        # Handle error responses
        if response.status_code != 200:
            error_code = data.get('code', 'unknown')
            error_message = data.get('message', 'No message')
            log_error(f"Notion OAuth failed: {error_code} - {error_message}")
            return None
        
        if 'error' in data:
            log_error(f"Notion OAuth error: {data.get('error')} - {data.get('error_description', '')}")
            return None
        
        access_token = data.get('access_token')
        if not access_token:
            log_error(f"Notion OAuth response missing access_token. Full response: {data}")
            return None
        
        log_info(f"Notion OAuth successful for workspace: {data.get('workspace_name')}")
        
        return {
            'access_token': access_token,
            'workspace_id': data.get('workspace_id'),
            'workspace_name': data.get('workspace_name'),
            'workspace_icon': data.get('workspace_icon'),
            'bot_id': data.get('bot_id'),
            'owner': data.get('owner', {}),
        }
    except Exception as e:
        log_error(f"Error exchanging Notion code: {e}")
        return None


def check_existing_notion_integration(workspace_id: str) -> Optional[Dict[str, Any]]:
    """Check if there's an existing Notion integration for this workspace"""
    supabase = get_supabase()
    
    result = supabase.table('workspace_integrations').select(
        'id, team_id, team_name, notion_workspace_name, notion_page_ids, slack_user_ids, connected_by_user_id, connected_by_name, is_active'
    ).eq('workspace_id', workspace_id).eq('provider', 'notion').execute()
    
    if not result.data:
        return None
    
    return result.data[0]


def save_workspace_notion_integration(
    workspace_id: str,
    notion_data: Dict[str, Any],
    connected_by_user_id: str,
    connected_by_name: str = None
) -> Dict[str, Any]:
    """
    Save Notion integration details for a workspace.
    
    If an integration already exists:
    - If same Notion workspace: adds the new user as a connected co-founder
    - If different Notion workspace: raises NotionWorkspaceMismatchError
    """
    supabase = get_supabase()
    
    new_notion_workspace_id = notion_data.get('workspace_id')
    new_notion_workspace_name = notion_data.get('workspace_name')
    
    # Check for existing integration
    existing = supabase.table('workspace_integrations').select(
        'id, team_id, team_name, notion_page_ids, slack_user_ids, is_active'
    ).eq('workspace_id', workspace_id).eq('provider', 'notion').execute()
    
    if existing.data and existing.data[0].get('is_active'):
        existing_integration = existing.data[0]
        existing_workspace_id = existing_integration.get('team_id')
        existing_workspace_name = existing_integration.get('team_name') or existing_integration.get('notion_workspace_name')
        
        # Check if it's the same Notion workspace
        if existing_workspace_id and existing_workspace_id != new_notion_workspace_id:
            raise NotionWorkspaceMismatchError(existing_workspace_name, new_notion_workspace_name)
        
        # Same workspace - add this user to connected users
        slack_user_ids = existing_integration.get('slack_user_ids') or []
        
        # Check if user is already connected
        already_connected = any(
            u.get('clerk_user_id') == connected_by_user_id 
            for u in slack_user_ids
        )
        
        if not already_connected:
            slack_user_ids.append({
                'clerk_user_id': connected_by_user_id,
                'notion_user_id': notion_data.get('owner', {}).get('user', {}).get('id'),
                'name': connected_by_name,
                'connected_at': datetime.now().isoformat(),
            })
        
        # Update with new user added
        result = supabase.table('workspace_integrations').update({
            'slack_user_ids': slack_user_ids,  # Reusing this field for connected users
            'access_token': notion_data['access_token'],  # Update token
        }).eq('id', existing_integration['id']).execute()
        
        if not result.data:
            raise ValueError("Failed to update Notion integration")
        
        log_info(f"Added co-founder to existing Notion integration for workspace {workspace_id}")
        return result.data[0]
    
    # No existing integration or inactive - create new one
    connected_users = [{
        'clerk_user_id': connected_by_user_id,
        'notion_user_id': notion_data.get('owner', {}).get('user', {}).get('id'),
        'name': connected_by_name,
        'connected_at': datetime.now().isoformat(),
    }]
    
    integration_data = {
        'workspace_id': workspace_id,
        'provider': 'notion',
        'access_token': notion_data['access_token'],
        'team_id': new_notion_workspace_id,  # Using team_id for Notion workspace ID
        'team_name': new_notion_workspace_name,
        'notion_workspace_name': new_notion_workspace_name,
        'bot_user_id': notion_data.get('bot_id'),
        'slack_user_ids': connected_users,  # Reusing for connected users list
        'connected_by_user_id': connected_by_user_id,
        'connected_by_name': connected_by_name,
        'settings': {},
        'is_active': True,
    }
    
    if existing.data:
        # Reactivate existing integration
        result = supabase.table('workspace_integrations').update(
            integration_data
        ).eq('id', existing.data[0]['id']).execute()
    else:
        result = supabase.table('workspace_integrations').insert(
            integration_data
        ).execute()
    
    if not result.data:
        raise ValueError("Failed to save Notion integration")
    
    # Verify token was saved
    saved_token = result.data[0].get('access_token')
    if saved_token:
        log_info(f"Notion integration saved for workspace {workspace_id} with token (length: {len(saved_token)})")
    else:
        log_error(f"Notion integration saved but access_token is missing for workspace {workspace_id}")
    
    return result.data[0]


def get_workspace_notion_integration(workspace_id: str) -> Optional[Dict[str, Any]]:
    """Get Notion integration for a workspace"""
    supabase = get_supabase()
    
    result = supabase.table('workspace_integrations').select(
        'id, team_id, team_name, notion_workspace_name, notion_page_ids, settings, is_active, created_at, slack_user_ids, connected_by_user_id, connected_by_name'
    ).eq('workspace_id', workspace_id).eq('provider', 'notion').eq('is_active', True).execute()
    
    if not result.data:
        return None
    
    return result.data[0]


def get_workspace_notion_token(workspace_id: str) -> Optional[str]:
    """Get Notion access token for a workspace"""
    supabase = get_supabase()
    
    result = supabase.table('workspace_integrations').select(
        'access_token'
    ).eq('workspace_id', workspace_id).eq('provider', 'notion').eq('is_active', True).execute()
    
    if not result.data:
        log_error(f"No Notion integration found for workspace {workspace_id}")
        return None
    
    token = result.data[0].get('access_token')
    if not token:
        log_error(f"Notion integration exists but access_token is NULL for workspace {workspace_id}")
    
    return token


def _notion_request(access_token: str, method: str, endpoint: str, data: Dict = None) -> Optional[Dict]:
    """Make a request to Notion API"""
    try:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Notion-Version': NOTION_VERSION,
        }
        
        url = f"{NOTION_API_BASE}{endpoint}"
        
        if method == 'GET':
            response = requests.get(url, headers=headers)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data)
        elif method == 'PATCH':
            response = requests.patch(url, headers=headers, json=data)
        else:
            return None
        
        result = response.json()
        
        if 'error' in result or result.get('object') == 'error':
            log_error(f"Notion API error: {result}")
            return None
        
        return result
    except Exception as e:
        log_error(f"Error making Notion request: {e}")
        return None


def create_partnership_workspace(
    workspace_id: str,
    partnership_name: str,
    founder_names: List[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Create the partnership workspace structure in Notion.
    Creates a main page with databases for Tasks, Decisions, and Meeting Notes.
    """
    access_token = get_workspace_notion_token(workspace_id)
    if not access_token:
        log_error(f"No Notion token for workspace {workspace_id}. Check that OAuth completed successfully.")
        return None
    
    try:
        # First, search for a page we can add content to
        # The integration needs a page selected during OAuth that we can write to
        log_info(f"Searching for accessible pages in Notion for workspace {workspace_id}")
        search_result = _notion_request(access_token, 'POST', '/search', {
            'filter': {'property': 'object', 'value': 'page'},
            'page_size': 10
        })
        
        if not search_result:
            log_error("Notion search API returned None - check access token validity")
            return None
            
        if not search_result.get('results'):
            log_error(f"No accessible pages found. User may not have selected pages during OAuth. Search response: {search_result}")
            return None
        
        log_info(f"Found {len(search_result.get('results', []))} accessible pages in Notion")
        
        # Get the parent page ID (first accessible page)
        parent_page = search_result['results'][0]
        parent_id = parent_page['id']
        log_info(f"Using parent page: {parent_page.get('properties', {}).get('title', {})}")
        
        # Create the main Partnership Hub page
        partnership_page = _notion_request(access_token, 'POST', '/pages', {
            'parent': {'page_id': parent_id},
            'icon': {'type': 'emoji', 'emoji': '🤝'},
            'properties': {
                'title': {
                    'title': [{'text': {'content': f'Guild Space: {partnership_name}'}}]
                }
            },
            'children': [
                {
                    'object': 'block',
                    'type': 'callout',
                    'callout': {
                        'icon': {'type': 'emoji', 'emoji': '🚀'},
                        'rich_text': [{
                            'type': 'text',
                            'text': {'content': f'Partnership workspace for {", ".join(founder_names) if founder_names else "co-founders"}. Managed by Guild Space.'}
                        }]
                    }
                },
                {
                    'object': 'block',
                    'type': 'divider',
                    'divider': {}
                },
                {
                    'object': 'block',
                    'type': 'heading_2',
                    'heading_2': {
                        'rich_text': [{'type': 'text', 'text': {'content': '📋 Quick Links'}}]
                    }
                },
                {
                    'object': 'block',
                    'type': 'paragraph',
                    'paragraph': {
                        'rich_text': [{
                            'type': 'text',
                            'text': {
                                'content': 'Open Guild Space Dashboard',
                                'link': {'url': f'{FRONTEND_URL}/workspaces/{workspace_id}'}
                            }
                        }]
                    }
                },
            ]
        })
        
        if not partnership_page:
            log_error("Failed to create partnership page")
            return None
        
        partnership_page_id = partnership_page['id']
        
        # Create Tasks Database
        tasks_db = _create_tasks_database(access_token, partnership_page_id, partnership_name)
        tasks_db_id = tasks_db['id'] if tasks_db else None
        
        # Create Decisions Database
        decisions_db = _create_decisions_database(access_token, partnership_page_id, partnership_name)
        decisions_db_id = decisions_db['id'] if decisions_db else None
        
        # Create Meeting Notes Database
        notes_db = _create_meeting_notes_database(access_token, partnership_page_id, partnership_name)
        notes_db_id = notes_db['id'] if notes_db else None
        
        # Save page IDs to database
        notion_page_ids = {
            'partnership_page_id': partnership_page_id,
            'tasks_db_id': tasks_db_id,
            'decisions_db_id': decisions_db_id,
            'notes_db_id': notes_db_id,
        }
        
        supabase = get_supabase()
        supabase.table('workspace_integrations').update({
            'notion_page_ids': notion_page_ids,
        }).eq('workspace_id', workspace_id).eq('provider', 'notion').execute()
        
        log_info(f"Created Notion partnership workspace for {workspace_id}")
        
        return {
            'partnership_page_id': partnership_page_id,
            'partnership_page_url': partnership_page.get('url'),
            'tasks_db_id': tasks_db_id,
            'decisions_db_id': decisions_db_id,
            'notes_db_id': notes_db_id,
        }
        
    except Exception as e:
        log_error(f"Error creating Notion partnership workspace: {e}")
        return None


def _create_tasks_database(access_token: str, parent_page_id: str, partnership_name: str) -> Optional[Dict]:
    """Create a Tasks database"""
    return _notion_request(access_token, 'POST', '/databases', {
        'parent': {'type': 'page_id', 'page_id': parent_page_id},
        'icon': {'type': 'emoji', 'emoji': '✅'},
        'title': [{'type': 'text', 'text': {'content': 'Tasks'}}],
        'properties': {
            'Task': {'title': {}},
            'Status': {
                'select': {
                    'options': [
                        {'name': 'To Do', 'color': 'gray'},
                        {'name': 'In Progress', 'color': 'blue'},
                        {'name': 'Done', 'color': 'green'},
                        {'name': 'Blocked', 'color': 'red'},
                    ]
                }
            },
            'Assignee': {
                'select': {
                    'options': []  # Will be populated with co-founder names
                }
            },
            'Priority': {
                'select': {
                    'options': [
                        {'name': 'High', 'color': 'red'},
                        {'name': 'Medium', 'color': 'yellow'},
                        {'name': 'Low', 'color': 'gray'},
                    ]
                }
            },
            'Due Date': {'date': {}},
            'Category': {
                'select': {
                    'options': [
                        {'name': 'Product', 'color': 'purple'},
                        {'name': 'Marketing', 'color': 'pink'},
                        {'name': 'Operations', 'color': 'orange'},
                        {'name': 'Finance', 'color': 'green'},
                        {'name': 'Legal', 'color': 'gray'},
                    ]
                }
            },
        }
    })


def _create_decisions_database(access_token: str, parent_page_id: str, partnership_name: str) -> Optional[Dict]:
    """Create a Decisions database"""
    return _notion_request(access_token, 'POST', '/databases', {
        'parent': {'type': 'page_id', 'page_id': parent_page_id},
        'icon': {'type': 'emoji', 'emoji': '🎯'},
        'title': [{'type': 'text', 'text': {'content': 'Decisions'}}],
        'properties': {
            'Decision': {'title': {}},
            'Status': {
                'select': {
                    'options': [
                        {'name': 'Proposed', 'color': 'yellow'},
                        {'name': 'Approved', 'color': 'green'},
                        {'name': 'Rejected', 'color': 'red'},
                        {'name': 'Revisiting', 'color': 'blue'},
                    ]
                }
            },
            'Category': {
                'select': {
                    'options': [
                        {'name': 'Strategy', 'color': 'purple'},
                        {'name': 'Product', 'color': 'blue'},
                        {'name': 'Hiring', 'color': 'green'},
                        {'name': 'Finance', 'color': 'orange'},
                        {'name': 'Operations', 'color': 'gray'},
                    ]
                }
            },
            'Decided By': {'rich_text': {}},
            'Date': {'date': {}},
            'Impact': {
                'select': {
                    'options': [
                        {'name': 'High', 'color': 'red'},
                        {'name': 'Medium', 'color': 'yellow'},
                        {'name': 'Low', 'color': 'gray'},
                    ]
                }
            },
        }
    })


def _create_meeting_notes_database(access_token: str, parent_page_id: str, partnership_name: str) -> Optional[Dict]:
    """Create a Meeting Notes database"""
    return _notion_request(access_token, 'POST', '/databases', {
        'parent': {'type': 'page_id', 'page_id': parent_page_id},
        'icon': {'type': 'emoji', 'emoji': '📝'},
        'title': [{'type': 'text', 'text': {'content': 'Meeting Notes'}}],
        'properties': {
            'Meeting': {'title': {}},
            'Date': {'date': {}},
            'Type': {
                'select': {
                    'options': [
                        {'name': 'Weekly Sync', 'color': 'blue'},
                        {'name': 'Strategy', 'color': 'purple'},
                        {'name': 'Retrospective', 'color': 'green'},
                        {'name': 'Planning', 'color': 'orange'},
                        {'name': 'Ad-hoc', 'color': 'gray'},
                    ]
                }
            },
            'Attendees': {'rich_text': {}},
            'Action Items': {'number': {}},
        }
    })


def disconnect_notion(workspace_id: str) -> bool:
    """Disconnect Notion integration for a workspace"""
    supabase = get_supabase()
    
    try:
        supabase.table('workspace_integrations').update({
            'is_active': False,
            'access_token': None,
        }).eq('workspace_id', workspace_id).eq('provider', 'notion').execute()
        
        log_info(f"Notion disconnected for workspace {workspace_id}")
        return True
    except Exception as e:
        log_error(f"Error disconnecting Notion: {e}")
        return False


def get_partnership_page_url(workspace_id: str) -> Optional[str]:
    """Get the URL to the Notion partnership page"""
    integration = get_workspace_notion_integration(workspace_id)
    if not integration:
        return None
    
    page_ids = integration.get('notion_page_ids', {})
    partnership_page_id = page_ids.get('partnership_page_id')
    
    if not partnership_page_id:
        return None
    
    # Construct Notion URL
    clean_id = partnership_page_id.replace('-', '')
    return f"https://notion.so/{clean_id}"


def is_user_connected_to_notion(workspace_id: str, clerk_user_id: str) -> bool:
    """Check if a specific user has connected their Notion account"""
    integration = get_workspace_notion_integration(workspace_id)
    if not integration:
        return False
    
    connected_users = integration.get('slack_user_ids') or []
    return any(u.get('clerk_user_id') == clerk_user_id for u in connected_users)
