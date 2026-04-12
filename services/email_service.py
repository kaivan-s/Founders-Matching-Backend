"""
Email Service using Resend
Handles all transactional emails for Guild Space
"""
import os
import resend
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Resend Configuration
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
EMAIL_FROM_ADDRESS = os.getenv('EMAIL_FROM_ADDRESS', 'notifications@guild-space.co')
EMAIL_FROM_NAME = os.getenv('EMAIL_FROM_NAME', 'Guild Space')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://guild-space.co')

# Initialize Resend
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
) -> bool:
    """
    Send an email via Resend
    Returns True if successful, False otherwise
    """
    if not to_email:
        logger.warning("No email address provided")
        return False
    
    # Skip in development if Resend not configured
    if not RESEND_API_KEY:
        logger.info(f"[DEV] Would send email to {to_email}: {subject}")
        return True
    
    try:
        response = resend.Emails.send({
            "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>",
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        })
        
        logger.info(f"Email sent to {to_email}: {response.get('id')}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        return False


# ============================================================
# Email Templates
# ============================================================

def _base_template(content: str, preview_text: str = "") -> str:
    """Base email template with Guild Space branding"""
    return f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Guild Space</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
    <!-- Preview text -->
    <div style="display: none; max-height: 0; overflow: hidden;">
        {preview_text}
    </div>
    
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color: #f8fafc;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width: 560px; background-color: #ffffff; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="padding: 32px 40px 24px; border-bottom: 1px solid #e2e8f0;">
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                                <tr>
                                    <td>
                                        <span style="font-size: 24px; font-weight: 700; color: #0d9488;">Guild</span>
                                        <span style="font-size: 24px; font-weight: 700; color: #1e3a8a;">Space</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Content -->
                    <tr>
                        <td style="padding: 32px 40px;">
                            {content}
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 24px 40px; background-color: #f8fafc; border-top: 1px solid #e2e8f0; border-radius: 0 0 12px 12px;">
                            <p style="margin: 0; font-size: 12px; color: #94a3b8; text-align: center;">
                                You're receiving this because you have an account on Guild Space.
                                <br>
                                <a href="{FRONTEND_URL}/settings" style="color: #64748b;">Manage email preferences</a>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
'''


# ============================================================
# Notification Emails
# ============================================================

def send_new_match_email(
    to_email: str,
    user_name: str,
    partner_name: str,
    partner_project: str,
    workspace_id: str
) -> bool:
    """Send email when two founders match"""
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            🎉 You have a new match!
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Great news, {user_name}! You've matched with <strong>{partner_name}</strong> 
            on their project <strong>"{partner_project}"</strong>.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            A shared workspace has been created where you can discuss equity, 
            define roles, and start building together.
        </p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/workspaces/{workspace_id}" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Go to Workspace →
                    </a>
                </td>
            </tr>
        </table>
        <p style="margin: 24px 0 0; font-size: 14px; color: #94a3b8;">
            Tip: Start by introducing yourself and discussing your vision for the project!
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"🎉 You matched with {partner_name}!",
        html_body=_base_template(content, f"You matched with {partner_name} on Guild Space")
    )


def send_interest_received_email(
    to_email: str,
    user_name: str,
    interested_user_name: str,
    project_name: str
) -> bool:
    """Send email when someone shows interest in your project"""
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            Someone's interested in your project!
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, <strong>{interested_user_name}</strong> is interested in 
            joining your project <strong>"{project_name}"</strong>.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Check out their profile and swipe right if you'd like to connect!
        </p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/interested" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        View Interested Founders →
                    </a>
                </td>
            </tr>
        </table>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"👋 {interested_user_name} is interested in {project_name}",
        html_body=_base_template(content, f"{interested_user_name} wants to join your project")
    )


def send_access_request_email(
    to_email: str,
    user_name: str,
    requester_name: str,
    project_name: str,
    request_message: Optional[str] = None
) -> bool:
    """Send email when someone requests access to view project details"""
    message_html = ""
    if request_message:
        message_html = f'''
        <div style="margin: 0 0 24px; padding: 16px; background-color: #f8fafc; border-radius: 8px; border-left: 4px solid #0d9488;">
            <p style="margin: 0; font-size: 14px; color: #64748b; font-style: italic;">
                "{request_message}"
            </p>
        </div>
        '''
    
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            🔒 Access Request for Your Project
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, <strong>{requester_name}</strong> is requesting access to view 
            the full details of your project <strong>"{project_name}"</strong>.
        </p>
        {message_html}
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/access-requests" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Review Request →
                    </a>
                </td>
            </tr>
        </table>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"🔒 {requester_name} requested access to {project_name}",
        html_body=_base_template(content, f"{requester_name} wants to view your project details")
    )


def send_access_granted_email(
    to_email: str,
    user_name: str,
    project_name: str,
    owner_name: str
) -> bool:
    """Send email when project owner grants access"""
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            ✅ Access Granted!
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Great news, {user_name}! <strong>{owner_name}</strong> has granted you access 
            to view their project <strong>"{project_name}"</strong>.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            You can now see the full project details and decide if you'd like to connect.
        </p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/discover" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        View Project →
                    </a>
                </td>
            </tr>
        </table>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"✅ Access granted to {project_name}",
        html_body=_base_template(content, f"You can now view {project_name}")
    )


def send_weekly_checkin_reminder_email(
    to_email: str,
    user_name: str,
    workspace_title: str,
    workspace_id: str,
    partner_name: str,
    days_since_last: Optional[int] = None
) -> bool:
    """Send weekly check-in reminder"""
    days_text = ""
    if days_since_last:
        days_text = f"It's been {days_since_last} days since your last check-in. "
    
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            📝 Time for your weekly check-in!
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, it's time for your weekly check-in with <strong>{partner_name}</strong> 
            on <strong>"{workspace_title}"</strong>.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            {days_text}Weekly check-ins take just 2 minutes and help keep your partnership healthy.
        </p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/workspaces/{workspace_id}/overview" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Complete Check-in →
                    </a>
                </td>
            </tr>
        </table>
        <p style="margin: 24px 0 0; font-size: 14px; color: #94a3b8;">
            Quick reminder: Share what you accomplished, any blockers, and your focus for next week.
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"📝 Weekly check-in reminder for {workspace_title}",
        html_body=_base_template(content, "Time for your weekly partnership check-in")
    )


def send_partner_checkin_submitted_email(
    to_email: str,
    user_name: str,
    partner_name: str,
    workspace_title: str,
    workspace_id: str,
    partner_health_emoji: str
) -> bool:
    """Send email when partner submits their check-in"""
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            {partner_health_emoji} {partner_name} submitted their check-in
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, your co-founder <strong>{partner_name}</strong> just submitted 
            their weekly check-in for <strong>"{workspace_title}"</strong>.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            See what they accomplished and what they're focusing on next week.
        </p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/workspaces/{workspace_id}/overview" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        View Check-in →
                    </a>
                </td>
            </tr>
        </table>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"{partner_health_emoji} {partner_name} submitted their weekly check-in",
        html_body=_base_template(content, f"{partner_name} completed their check-in")
    )


def send_advisor_request_email(
    to_email: str,
    advisor_name: str,
    founder_name: str,
    project_name: str,
    message: Optional[str] = None
) -> bool:
    """Send email to advisor when a founder requests their help"""
    message_html = ""
    if message:
        message_html = f'''
        <div style="margin: 0 0 24px; padding: 16px; background-color: #f8fafc; border-radius: 8px; border-left: 4px solid #0d9488;">
            <p style="margin: 0; font-size: 14px; color: #64748b; font-style: italic;">
                "{message}"
            </p>
        </div>
        '''
    
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            🤝 New Advisor Request
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {advisor_name}, <strong>{founder_name}</strong> is requesting your guidance 
            on their project <strong>"{project_name}"</strong>.
        </p>
        {message_html}
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/advisor/dashboard" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Review Request →
                    </a>
                </td>
            </tr>
        </table>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"🤝 {founder_name} wants you as an advisor for {project_name}",
        html_body=_base_template(content, f"New advisor request from {founder_name}")
    )


def send_welcome_email(
    to_email: str,
    user_name: str
) -> bool:
    """Send welcome email to new users"""
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            Welcome to Guild Space! 🚀
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, we're excited to have you on board!
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Guild Space helps you find the perfect co-founder for your side project. 
            Here's how to get started:
        </p>
        <ol style="margin: 0 0 24px; padding-left: 24px; color: #475569; line-height: 1.8;">
            <li><strong>Create a project</strong> – Share your idea and what you're looking for</li>
            <li><strong>Discover founders</strong> – Swipe through compatible co-founders</li>
            <li><strong>Match & build</strong> – Start working together in a shared workspace</li>
        </ol>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/discover" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Start Exploring →
                    </a>
                </td>
            </tr>
        </table>
        <p style="margin: 24px 0 0; font-size: 14px; color: #94a3b8;">
            Questions? Just reply to this email – we're here to help!
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"Welcome to Guild Space, {user_name}! 🚀",
        html_body=_base_template(content, "Welcome to Guild Space - find your perfect co-founder")
    )


def send_new_projects_digest_email(
    to_email: str,
    user_name: str,
    projects: list,
    total_new_projects: int
) -> bool:
    """Send weekly digest of new projects"""
    
    # Build project cards HTML
    project_cards = ""
    for project in projects[:5]:  # Show max 5 projects
        project_cards += f'''
        <div style="margin-bottom: 16px; padding: 16px; background-color: #f8fafc; border-radius: 8px; border-left: 4px solid #0d9488;">
            <h3 style="margin: 0 0 8px; font-size: 16px; font-weight: 600; color: #0f172a;">
                {project.get('title', 'Untitled Project')}
            </h3>
            <p style="margin: 0 0 8px; font-size: 14px; color: #64748b; line-height: 1.5;">
                {project.get('description', '')[:120]}{'...' if len(project.get('description', '')) > 120 else ''}
            </p>
            <p style="margin: 0; font-size: 12px; color: #94a3b8;">
                by {project.get('founder_name', 'A founder')} · {project.get('stage', 'Idea stage')}
            </p>
        </div>
        '''
    
    more_text = ""
    if total_new_projects > 5:
        more_text = f'<p style="margin: 0 0 24px; font-size: 14px; color: #64748b;">...and {total_new_projects - 5} more new projects!</p>'
    
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            🚀 {total_new_projects} New Project{'s' if total_new_projects != 1 else ''} This Week
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, check out what's new on Guild Space this week!
        </p>
        
        {project_cards}
        {more_text}
        
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/discover" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Discover Projects →
                    </a>
                </td>
            </tr>
        </table>
        <p style="margin: 24px 0 0; font-size: 14px; color: #94a3b8;">
            Find your next co-founder and start building together!
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"🚀 {total_new_projects} new project{'s' if total_new_projects != 1 else ''} to explore this week",
        html_body=_base_template(content, f"{total_new_projects} new projects added on Guild Space")
    )
