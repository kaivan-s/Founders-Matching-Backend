"""Notification and approval service for workspace events"""
import boto3
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from config.database import get_supabase
import json

class NotificationService:
    """Handle notifications and approval workflows"""
    
    def __init__(self):
        self.supabase = get_supabase()
        
        # Get AWS configuration from environment
        aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        aws_region = os.environ.get('AWS_REGION', 'us-east-1')
        self.from_email = os.environ.get('SES_FROM_EMAIL', 'noreply@yourapp.com')
        self.from_name = os.environ.get('SES_FROM_NAME', 'Founders Matching')
        
        # Initialize AWS SES client
        if aws_access_key and aws_secret_key:
            # Use explicit credentials from environment
            self.ses_client = boto3.client(
                'ses',
                region_name=aws_region,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key
            )
        else:
            # Fallback to IAM role/default credentials (for EC2/ECS/Lambda)
            self.ses_client = boto3.client('ses', region_name=aws_region)
        
    def _get_founder_id(self, clerk_user_id: str, email: str = None) -> str:
        """Get founder ID from clerk_user_id.
        If not found by clerk_user_id and email is provided, checks for existing founder by email
        and updates clerk_user_id to link accounts.
        """
        result = self.supabase.table('founders').select('id, email').eq('clerk_user_id', clerk_user_id).execute()
        
        if not result.data:
            # If email is provided, check for existing founder by email (case-insensitive)
            if email and email.strip():
                email_lower = email.strip().lower()
                all_founders = self.supabase.table('founders').select('id, email, clerk_user_id').execute()
                if all_founders.data:
                    for founder in all_founders.data:
                        founder_email = founder.get('email', '').strip().lower()
                        if founder_email == email_lower:
                            # Found existing founder with same email - update clerk_user_id
                            self.supabase.table('founders').update({'clerk_user_id': clerk_user_id}).eq('id', founder['id']).execute()
                            return founder['id']
            
            raise ValueError("Founder not found")
        return result.data[0]['id']
    
    def _get_workspace_participants(self, workspace_id: str) -> List[Dict]:
        """Get all participants in a workspace"""
        result = self.supabase.table('workspace_participants').select(
            'user_id, title, founders!workspace_participants_user_id_fkey(id, name, email)'
        ).eq('workspace_id', workspace_id).execute()
        return result.data if result.data else []
    
    def create_notification(
        self,
        workspace_id: str,
        recipient_id: str,
        actor_id: str,
        event_type: str,
        title: str,
        message: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        approval_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> str:
        """Create a notification and optionally queue email"""
        
        # Create notification
        notification_data = {
            'workspace_id': workspace_id,
            'user_id': recipient_id,
            'actor_user_id': actor_id,
            'type': event_type,
            'title': title,
            'message': message,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'approval_id': approval_id,
            'metadata': metadata or {}
        }
        
        result = self.supabase.table('notifications').insert(notification_data).execute()
        notification_id = result.data[0]['id'] if result.data else None
        
        # Check notification preferences and queue email if needed
        if notification_id:
            self._check_and_queue_email(workspace_id, recipient_id, event_type, notification_id)
        
        return notification_id
    
    def _check_and_queue_email(
        self, 
        workspace_id: str, 
        recipient_id: str, 
        event_type: str,
        notification_id: str
    ):
        """Check user preferences and queue email if needed"""
        
        # PHASE 1: Email disabled for now
        email_enabled = os.environ.get('EMAIL_ENABLED', 'false').lower() == 'true'
        if not email_enabled:
            return  # Skip email queuing
        
        # Get user preferences
        prefs = self.supabase.table('notification_preferences').select('*').eq(
            'user_id', recipient_id
        ).eq('workspace_id', workspace_id).execute()
        
        # Default preferences if not set
        email_enabled = True
        email_digest = False
        approval_emails = True
        
        if prefs.data:
            pref = prefs.data[0]
            email_enabled = pref.get('email_enabled', True)
            email_digest = pref.get('email_digest', False)
            approval_emails = pref.get('approval_emails', True)
        
        # Determine if we should send email
        is_approval = event_type in ['APPROVAL_REQUESTED', 'APPROVAL_COMPLETED']
        should_send = email_enabled and (
            (is_approval and approval_emails) or 
            (not is_approval and not email_digest)
        )
        
        if should_send:
            # Get notification details
            notification = self.supabase.table('notifications').select(
                '*, actor:founders!notifications_actor_user_id_fkey(name), '
                'recipient:founders!notifications_user_id_fkey(email, name)'
            ).eq('id', notification_id).execute()
            
            if notification.data:
                notif = notification.data[0]
                self._queue_email(
                    to_email=notif['recipient']['email'],
                    subject=self._generate_email_subject(event_type, workspace_id),
                    template_name='notification',
                    template_data={
                        'notification': notif,
                        'workspace_id': workspace_id,
                        'is_approval': is_approval
                    },
                    workspace_id=workspace_id,
                    notification_id=notification_id
                )
    
    def _generate_email_subject(self, event_type: str, workspace_id: str) -> str:
        """Generate email subject based on event type"""
        
        # Get workspace name
        workspace = self.supabase.table('workspaces').select('name').eq('id', workspace_id).execute()
        workspace_name = workspace.data[0]['name'] if workspace.data else 'Your workspace'
        
        subjects = {
            'APPROVAL_REQUESTED': f"Action required: Approval needed in {workspace_name}",
            'EQUITY_PROPOSAL_CREATED': f"New equity proposal in {workspace_name}",
            'FOUNDER_TITLE_PROPOSAL': f"Title change proposed in {workspace_name}",
            'DECISION_CREATED': f"New decision added in {workspace_name}",
            'KPI_UPDATED': f"KPI updated in {workspace_name}",
            'CHECKIN_CREATED': f"New check-in posted in {workspace_name}"
        }
        
        return subjects.get(event_type, f"Update in {workspace_name}")
    
    def _queue_email(
        self,
        to_email: str,
        subject: str,
        template_name: str,
        template_data: Dict,
        workspace_id: Optional[str] = None,
        notification_id: Optional[str] = None
    ):
        """Queue email for async processing"""
        
        body = self._render_email_template(template_name, template_data)
        
        email_data = {
            'to_email': to_email,
            'subject': subject,
            'body': body,
            'template_name': template_name,
            'template_data': template_data,
            'workspace_id': workspace_id,
            'notification_id': notification_id
        }
        
        self.supabase.table('email_queue').insert(email_data).execute()
    
    def _render_email_template(self, template_name: str, data: Dict) -> str:
        """Render email template (simplified for now)"""
        
        if template_name == 'notification':
            notif = data['notification']
            is_approval = data['is_approval']
            
            if is_approval:
                return f"""
                <h2>Approval Required</h2>
                <p>{notif['title']}</p>
                <p>{notif.get('message', '')}</p>
                <p>
                    <a href="https://yourapp.com/workspace/{data['workspace_id']}/approvals">
                        Review and Approve
                    </a>
                </p>
                """
            else:
                return f"""
                <h2>Workspace Update</h2>
                <p><strong>{notif['actor']['name']}</strong> {notif['title']}</p>
                <p>{notif.get('message', '')}</p>
                <p>
                    <a href="https://yourapp.com/workspace/{data['workspace_id']}">
                        View in Workspace
                    </a>
                </p>
                """
        
        return "Notification from your workspace"
    
    def send_queued_emails(self, limit: int = 10) -> int:
        """Process queued emails (call from cron/scheduler)"""
        
        # Get pending emails
        emails = self.supabase.table('email_queue').select('*').eq(
            'status', 'PENDING'
        ).limit(limit).execute()
        
        sent_count = 0
        
        for email in emails.data or []:
            try:
                # Send via AWS SES
                response = self.ses_client.send_email(
                    Source=self.from_email,
                    Destination={'ToAddresses': [email['to_email']]},
                    Message={
                        'Subject': {'Data': email['subject']},
                        'Body': {'Html': {'Data': email['body']}}
                    }
                )
                
                # Mark as sent
                self.supabase.table('email_queue').update({
                    'status': 'SENT',
                    'sent_at': datetime.now().isoformat()
                }).eq('id', email['id']).execute()
                
                sent_count += 1
                
            except Exception as e:
                # Mark as failed
                self.supabase.table('email_queue').update({
                    'status': 'FAILED',
                    'error_message': str(e)
                }).eq('id', email['id']).execute()
        
        return sent_count
    
    def send_daily_digest(self):
        """Send daily digest emails for users who opted in"""
        
        # Get users with digest preference
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        
        # Query users with digest enabled and unread notifications
        query = """
        SELECT DISTINCT n.user_id, n.workspace_id, u.email, u.name
        FROM notifications n
        JOIN notification_preferences p ON p.user_id = n.user_id AND p.workspace_id = n.workspace_id
        JOIN founders u ON u.id = n.user_id
        WHERE n.created_at > %s
        AND n.read_at IS NULL
        AND p.email_digest = true
        AND p.email_enabled = true
        """
        
        # This would need raw SQL support in Supabase client
        # For now, simplified version:
        prefs = self.supabase.table('notification_preferences').select(
            '*, founders!notification_preferences_user_id_fkey(email, name)'
        ).eq('email_digest', True).eq('email_enabled', True).execute()
        
        for pref in prefs.data or []:
            # Get unread notifications for this user/workspace
            notifications = self.supabase.table('notifications').select('*').eq(
                'user_id', pref['user_id']
            ).eq('workspace_id', pref['workspace_id']).is_('read_at', 'null').gte(
                'created_at', yesterday
            ).execute()
            
            if notifications.data:
                self._send_digest_email(
                    pref['founders']['email'],
                    pref['founders']['name'],
                    pref['workspace_id'],
                    notifications.data
                )
    
    def _send_digest_email(self, email: str, name: str, workspace_id: str, notifications: List[Dict]):
        """Send digest email with all notifications"""
        
        # Group by type
        by_type = {}
        for notif in notifications:
            event_type = notif['type']
            if event_type not in by_type:
                by_type[event_type] = []
            by_type[event_type].append(notif)
        
        # Build summary
        summary = []
        for event_type, items in by_type.items():
            count = len(items)
            type_name = event_type.replace('_', ' ').title()
            summary.append(f"{count} {type_name}")
        
        subject = f"Daily digest: {', '.join(summary)}"
        
        body = f"""
        <h2>Hi {name},</h2>
        <p>Here's your daily workspace activity summary:</p>
        <ul>
        """
        
        for notif in notifications:
            body += f"<li>{notif['title']}</li>"
        
        body += f"""
        </ul>
        <p>
            <a href="https://yourapp.com/workspace/{workspace_id}">
                View Workspace
            </a>
        </p>
        """
        
        self._queue_email(email, subject, 'digest', {
            'name': name,
            'notifications': notifications
        }, workspace_id)


class ApprovalService:
    """Handle approval workflows"""
    
    def __init__(self):
        self.supabase = get_supabase()
        self.notification_service = NotificationService()
    
    def create_approval(
        self,
        clerk_user_id: str,
        workspace_id: str,
        entity_type: str,
        entity_id: str,
        proposed_data: Dict,
        original_data: Optional[Dict] = None
    ) -> str:
        """Create an approval request"""
        
        # Get proposer ID
        proposer = self.supabase.table('founders').select('id, name').eq(
            'clerk_user_id', clerk_user_id
        ).execute()
        
        if not proposer.data:
            raise ValueError("Proposer not found")
        
        proposer_id = proposer.data[0]['id']
        proposer_name = proposer.data[0]['name']
        
        # Get other participant (approver)
        participants = self.supabase.table('workspace_participants').select(
            'user_id, founders!workspace_participants_user_id_fkey(id, name)'
        ).eq('workspace_id', workspace_id).execute()
        
        
        approver = None
        for p in participants.data or []:
            if p['user_id'] != proposer_id:
                approver = p.get('founders')
                if approver:
                    break
        
        if not approver:
            raise ValueError(f"No approver found in workspace. Found {len(participants.data or [])} participants")
        
        # Create approval record
        approval_data = {
            'workspace_id': workspace_id,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'proposed_by_user_id': proposer_id,
            'approver_user_id': approver['id'],
            'proposed_data': proposed_data,
            'original_data': original_data,
            'status': 'PENDING'
        }
        
        result = self.supabase.table('approvals').insert(approval_data).execute()
        
        if not result.data:
            raise ValueError("Failed to create approval")
        
        approval_id = result.data[0]['id']
        
        # Create notification for approver
        title = self._get_approval_title(entity_type, proposed_data, proposer_name)
        
        self.notification_service.create_notification(
            workspace_id=workspace_id,
            recipient_id=approver['id'],
            actor_id=proposer_id,
            event_type='APPROVAL_REQUESTED',
            title=title,
            message=f"{proposer_name} has requested your approval",
            entity_type=entity_type,
            entity_id=entity_id,
            approval_id=approval_id,
            metadata={'proposed_data': proposed_data}
        )
        
        return approval_id
    
    def _get_approval_title(self, entity_type: str, proposed_data: Dict, proposer_name: str) -> str:
        """Generate approval title based on type"""
        
        if entity_type == 'EQUITY_SCENARIO':
            return f"{proposer_name} proposed equity changes"
        elif entity_type == 'FOUNDER_TITLE':
            new_title = proposed_data.get('title', 'Unknown')
            return f"{proposer_name} wants to change title to {new_title}"
        elif entity_type == 'DECISION':
            title = proposed_data.get('title', 'a decision')
            return f"{proposer_name} added {title} (requires approval)"
        
        return f"{proposer_name} requested approval for {entity_type}"
    
    def process_approval(
        self,
        clerk_user_id: str,
        approval_id: str,
        decision: str,
        comment: Optional[str] = None
    ) -> bool:
        """Process an approval decision (approve/reject)"""
        
        # Get approver ID
        approver = self.supabase.table('founders').select('id, name').eq(
            'clerk_user_id', clerk_user_id
        ).execute()
        
        if not approver.data:
            raise ValueError("Approver not found")
        
        approver_id = approver.data[0]['id']
        approver_name = approver.data[0]['name']
        
        # Get approval details
        approval = self.supabase.table('approvals').select('*').eq('id', approval_id).execute()
        
        if not approval.data:
            raise ValueError("Approval not found")
        
        approval_data = approval.data[0]
        
        # Verify approver
        if approval_data['approver_user_id'] != approver_id:
            raise ValueError("You are not authorized to approve this request")
        
        if approval_data['status'] != 'PENDING':
            raise ValueError("This approval has already been processed")
        
        # Update approval
        status = 'APPROVED' if decision == 'approve' else 'REJECTED'
        
        self.supabase.table('approvals').update({
            'status': status,
            'decided_at': datetime.now().isoformat(),
            'decision_comment': comment
        }).eq('id', approval_id).execute()
        
        # Apply changes if approved
        if status == 'APPROVED':
            self._apply_approved_changes(approval_data)
        
        # Notify proposer
        self.notification_service.create_notification(
            workspace_id=approval_data['workspace_id'],
            recipient_id=approval_data['proposed_by_user_id'],
            actor_id=approver_id,
            event_type='APPROVAL_COMPLETED',
            title=f"{approver_name} {decision}d your proposal",
            message=comment,
            entity_type=approval_data['entity_type'],
            entity_id=approval_data['entity_id'],
            approval_id=approval_id,
            metadata={'status': status}
        )
        
        return True
    
    def _apply_approved_changes(self, approval: Dict):
        """Apply approved changes to entities"""
        
        entity_type = approval['entity_type']
        entity_id = approval['entity_id']
        proposed_data = approval['proposed_data']
        
        if entity_type == 'EQUITY_SCENARIO':
            # Check if we need to set this as current
            is_current = proposed_data.get('is_current', False)
            
            # Get workspace_id from the scenario
            scenario = self.supabase.table('workspace_equity_scenarios').select('workspace_id').eq('id', entity_id).execute()
            if scenario.data:
                workspace_id = scenario.data[0]['workspace_id']
                
                # If setting as current, mark all other scenarios as canceled
                if is_current:
                    # Set all other scenarios to not current and canceled
                    self.supabase.table('workspace_equity_scenarios').update({
                        'is_current': False,
                        'status': 'canceled'
                    }).eq('workspace_id', workspace_id).neq('id', entity_id).execute()
            
            # Update equity scenario with approved data
            update_data = {
                **proposed_data,
                'approval_status': 'APPROVED',
                'approval_id': approval['id'],
                'status': 'active'  # Approved scenarios are active
            }
            
            # If not setting as current, don't include is_current in update
            if not is_current:
                update_data.pop('is_current', None)
            
            self.supabase.table('workspace_equity_scenarios').update(update_data).eq('id', entity_id).execute()
            
        elif entity_type == 'FOUNDER_TITLE':
            # Update participant title
            self.supabase.table('workspace_participants').update({
                'title': proposed_data['title'],
                'title_approval_status': 'APPROVED',
                'title_approval_id': approval['id']
            }).eq('id', entity_id).execute()
            
        elif entity_type == 'DECISION':
            # Update decision
            self.supabase.table('workspace_decisions').update({
                **proposed_data,
                'approval_status': 'APPROVED',
                'approval_id': approval['id']
            }).eq('id', entity_id).execute()
    
    def get_pending_approvals(self, clerk_user_id: str, workspace_id: str) -> List[Dict]:
        """Get pending approvals for a user in a workspace"""
        
        user = self.supabase.table('founders').select('id').eq(
            'clerk_user_id', clerk_user_id
        ).execute()
        
        if not user.data:
            return []
        
        user_id = user.data[0]['id']
        
        # Get approvals where user is approver
        approvals = self.supabase.table('approvals').select(
            '*, proposer:founders!approvals_proposed_by_user_id_fkey(name)'
        ).eq('workspace_id', workspace_id).eq(
            'approver_user_id', user_id
        ).eq('status', 'PENDING').order('created_at', desc=True).execute()
        
        return approvals.data or []
