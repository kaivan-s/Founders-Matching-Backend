"""Message-related business logic"""
from config.database import get_supabase
from datetime import datetime
from services.notification_service import NotificationService

def get_messages(clerk_user_id, match_id):
    """Get all messages for a specific match"""
    supabase = get_supabase()
    
    # Get current user's founder ID
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    current_user_id = user_profile.data[0]['id']
    
    # Verify user is part of this match
    match = supabase.table('matches').select('*').eq('id', match_id).execute()
    if not match.data:
        raise ValueError("Match not found")
    
    match_data = match.data[0]
    if match_data['founder1_id'] != current_user_id and match_data['founder2_id'] != current_user_id:
        raise ValueError("You are not part of this match")
    
    # Get all messages for this match
    messages = supabase.table('messages').select('*').eq('match_id', match_id).order('created_at', desc=False).execute()
    
    return messages.data if messages.data else []

def send_message(clerk_user_id, match_id, content):
    """Send a message in a match"""
    supabase = get_supabase()
    
    if not content or not content.strip():
        raise ValueError("Message content cannot be empty")
    
    # Get current user's founder ID and name
    user_profile = supabase.table('founders').select('id, name').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Profile not found")
    
    current_user_id = user_profile.data[0]['id']
    sender_name = user_profile.data[0].get('name', 'Your partner')
    
    # Verify user is part of this match
    match = supabase.table('matches').select('*').eq('id', match_id).execute()
    if not match.data:
        raise ValueError("Match not found")
    
    match_data = match.data[0]
    if match_data['founder1_id'] != current_user_id and match_data['founder2_id'] != current_user_id:
        raise ValueError("You are not part of this match")
    
    # Determine recipient (the other founder in the match)
    recipient_id = match_data['founder2_id'] if match_data['founder1_id'] == current_user_id else match_data['founder1_id']
    
    # Get workspace_id from match
    workspace = supabase.table('workspaces').select('id').eq('match_id', match_id).execute()
    workspace_id = workspace.data[0]['id'] if workspace.data else None
    
    # Create message
    message_data = {
        'match_id': match_id,
        'sender_id': current_user_id,
        'content': content.strip()
    }
    
    response = supabase.table('messages').insert(message_data).execute()
    message = response.data[0] if response.data else None
    
    # Create notification for the recipient if workspace exists
    if message and workspace_id:
        try:
            notification_service = NotificationService()
            # Truncate message content for notification title
            message_preview = content.strip()[:50] + ('...' if len(content.strip()) > 50 else '')
            notification_service.create_notification(
                workspace_id=workspace_id,
                recipient_id=recipient_id,
                actor_id=current_user_id,
                event_type='MESSAGE_RECEIVED',
                title=f"New message from {sender_name}",
                message=message_preview,
                entity_type='MESSAGE',
                entity_id=message['id'],
                metadata={
                    'match_id': match_id,
                    'sender_name': sender_name,
                    'message_preview': message_preview
                }
            )
        except Exception as e:
            # Don't fail message sending if notification creation fails
            pass
    
    return message

def mark_messages_as_read(clerk_user_id, match_id):
    """Mark all messages in a match as read for the current user"""
    # Note: This is a placeholder - read functionality requires a 'read' column in messages table
    # For now, we'll just return success without actually marking
    # To enable: Add 'read' boolean column to messages table in Supabase
    return {"message": "Messages marked as read"}

def get_unread_count(clerk_user_id):
    """Get total unread message count for the current user"""
    # Note: Read functionality requires a 'read' column in messages table
    # For now, return 0 - can be implemented later when column is added
    # To enable: Add 'read' boolean column to messages table in Supabase
    return {"unread_count": 0}

