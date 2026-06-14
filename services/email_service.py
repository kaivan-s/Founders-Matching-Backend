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
    print(f"[EMAIL] send_email called: to={to_email}, subject={subject[:50]}...")
    
    if not to_email:
        print("[EMAIL] SKIP: No email address provided")
        logger.warning("No email address provided")
        return False
    
    # Skip in development if Resend not configured
    if not RESEND_API_KEY:
        print(f"[EMAIL] DEV MODE: Would send to {to_email}: {subject}")
        logger.info(f"[DEV] Would send email to {to_email}: {subject}")
        return True
    
    try:
        print(f"[EMAIL] Sending via Resend to {to_email}...")
        response = resend.Emails.send({
            "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>",
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        })
        
        print(f"[EMAIL] SUCCESS: Sent to {to_email}, id={response.get('id')}")
        logger.info(f"Email sent to {to_email}: {response.get('id')}")
        return True
        
    except Exception as e:
        print(f"[EMAIL] FAILED: {to_email} - {str(e)}")
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


def send_equity_approval_pending_email(
    to_email: str,
    user_name: str,
    partner_name: str,
    workspace_id: str,
    equity_percent: int = None
) -> bool:
    """Send email when partner has approved equity and waiting for your approval"""
    equity_text = f" ({equity_percent}% for you)" if equity_percent else ""
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            Your co-founder approved the equity split
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, <strong>{partner_name}</strong> has approved the proposed equity split{equity_text}.
            Please review and approve to finalize the agreement.
        </p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/workspaces/{workspace_id}/equity-roles" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Review & Approve →
                    </a>
                </td>
            </tr>
        </table>
        <p style="margin: 24px 0 0; font-size: 14px; color: #94a3b8;">
            Both founders must approve for the equity agreement to be finalized.
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"✅ {partner_name} approved the equity split - your turn!",
        html_body=_base_template(content, f"{partner_name} approved the equity agreement")
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


def send_advisor_approved_email(
    to_email: str,
    advisor_name: str
) -> bool:
    """Send email when an advisor's profile is approved"""
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            You're Approved! 🎉
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {advisor_name}, great news! Your advisor profile has been reviewed and approved.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            You're now visible in the Guild Space advisor marketplace. Founders can discover your profile 
            and book consultations with you.
        </p>
        <div style="margin: 0 0 24px; padding: 16px; background-color: #f0fdf4; border-radius: 8px; border-left: 4px solid #10b981;">
            <p style="margin: 0; font-size: 14px; color: #166534; font-weight: 500;">
                What's next?
            </p>
            <ul style="margin: 8px 0 0; padding-left: 20px; color: #166534; font-size: 14px;">
                <li>Make sure your calendar availability is up to date</li>
                <li>Check your consultation rates are set correctly</li>
                <li>Keep an eye out for booking requests!</li>
            </ul>
        </div>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/advisor/dashboard" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Go to Dashboard →
                    </a>
                </td>
            </tr>
        </table>
        <p style="margin: 24px 0 0; font-size: 14px; color: #94a3b8;">
            Thank you for joining Guild Space as an advisor!
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"You're approved as a Guild Space Advisor! 🎉",
        html_body=_base_template(content, "Your advisor profile is now live")
    )


def send_advisor_rejected_email(
    to_email: str,
    advisor_name: str,
    reason: Optional[str] = None
) -> bool:
    """Send email when an advisor's profile is rejected"""
    reason_html = ""
    if reason:
        reason_html = f'''
        <div style="margin: 0 0 24px; padding: 16px; background-color: #fef2f2; border-radius: 8px; border-left: 4px solid #ef4444;">
            <p style="margin: 0; font-size: 14px; color: #991b1b;">
                <strong>Feedback:</strong> {reason}
            </p>
        </div>
        '''
    
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            Profile Update Required
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {advisor_name}, thank you for applying to be an advisor on Guild Space.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            After reviewing your profile, we weren't able to approve it at this time. 
            This could be due to incomplete information or other factors.
        </p>
        {reason_html}
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            You're welcome to update your profile and resubmit for review.
        </p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #1e3a8a;">
                    <a href="{FRONTEND_URL}/advisor/onboarding" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Update Profile →
                    </a>
                </td>
            </tr>
        </table>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"Your Guild Space Advisor Application",
        html_body=_base_template(content, "Advisor profile update required")
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
            Have feedback or suggestions? We'd love to hear from you — share your thoughts directly on the platform!
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


def send_discovery_ready_email(to_email: str, user_name: str, user_plan: str = 'FREE') -> bool:
    """Let a seeker know their personalized opportunities are ready."""
    
    # Tier-specific messaging
    if user_plan == 'PRO_PLUS':
        opportunities_text = "50 personalized opportunities"
        upgrade_text = ""
    elif user_plan == 'PRO':
        opportunities_text = "25 personalized opportunities"
        upgrade_text = ""
    else:
        opportunities_text = "5 personalized opportunities"
        upgrade_text = '''
            <p style="margin: 24px 0 0; font-size: 14px; color: #64748b; line-height: 1.6;">
                Want more? <a href="{}/pricing" style="color: #0d9488; text-decoration: none; font-weight: 600;">Upgrade to Pro</a> for 5x more opportunities and unlimited applications.
            </p>
        '''.format(FRONTEND_URL)
    
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            Your opportunities are ready
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, we've matched you with {opportunities_text} based on your preferences.
            Each one is ranked by compatibility — browse them when you have a minute.
        </p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/discover"
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        Browse Opportunities →
                    </a>
                </td>
            </tr>
        </table>
        {upgrade_text}
    '''
    return send_email(
        to_email=to_email,
        subject="Your personalized opportunities are ready",
        html_body=_base_template(content, "Opportunities matched for you on Guild Space"),
    )


def send_discovery_daily_matches_ready_email(to_email: str, user_name: str) -> bool:
    """DEPRECATED: Use send_discovery_ready_email instead."""
    return send_discovery_ready_email(to_email, user_name, 'FREE')


def send_workspace_week_one_checkin_email(
    to_email: str,
    user_name: str,
    partner_name: str,
    workspace_title: str,
    workspace_id: str
) -> bool:
    """Send 1-week check-in email after workspace creation to encourage founders to complete setup."""
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            How's your first week going?
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {user_name}, it's been a week since you matched with <strong>{partner_name}</strong>!
            We'd love to know how things are progressing.
        </p>
        
        <div style="background-color: #f8fafc; border-radius: 12px; padding: 24px; margin: 0 0 24px;">
            <p style="margin: 0 0 16px; font-size: 16px; font-weight: 600; color: #0f172a;">
                Quick check-in:
            </p>
            <ul style="margin: 0; padding-left: 20px; color: #475569; line-height: 1.8;">
                <li>Have you had your first conversation?</li>
                <li>Did you define your roles and responsibilities?</li>
                <li>Have you discussed the equity split?</li>
                <li>What's your next milestone together?</li>
            </ul>
        </div>
        
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Your workspace has tools to help you align on equity, define roles, and track progress.
            Take a few minutes to fill in the details — it'll make your partnership stronger!
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
            Need help? Reply to this email and we'll assist you.
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"📊 Week 1 with {partner_name} — how's it going?",
        html_body=_base_template(content, f"Your first week with {partner_name}")
    )


def send_campaign_discovery_email(
    to_email: str,
    user_name: str,
) -> bool:
    """
    Send campaign email encouraging user to check new projects in their feed.
    Part of the rotating daily campaign system.
    """
    # Use first name only
    first_name = (user_name or 'there').split()[0] if user_name else 'there'
    
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            New projects are waiting for you
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {first_name}, new projects have been added to Guild Space that might be a great fit for your skills and interests.
        </p>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Take a moment to explore and find your next opportunity to build something meaningful.
        </p>
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
            You're receiving this because you have an account on Guild Space.
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject="🔍 New projects are waiting for you",
        html_body=_base_template(content, "New projects match your profile on Guild Space")
    )


def send_dissolution_request_email(
    to_email: str,
    user_name: str,
    requester_name: str,
    workspace_id: str,
    cooloff_ends_at,
    reason: str = None
) -> bool:
    """
    Send email when a co-founder requests partnership dissolution.
    """
    first_name = (user_name or 'there').split()[0] if user_name else 'there'
    
    # Format the date
    if hasattr(cooloff_ends_at, 'strftime'):
        end_date = cooloff_ends_at.strftime('%B %d, %Y')
    else:
        end_date = str(cooloff_ends_at)[:10] if cooloff_ends_at else 'soon'
    
    reason_block = ""
    if reason:
        reason_block = f'''
        <div style="background: #fef3c7; border-radius: 8px; padding: 16px; margin: 0 0 24px;">
            <p style="margin: 0; font-size: 14px; color: #92400e;">
                <strong>Their reason:</strong> {reason}
            </p>
        </div>
        '''
    
    content = f'''
        <h1 style="margin: 0 0 16px; font-size: 24px; font-weight: 600; color: #0f172a;">
            {requester_name} has requested to end your partnership
        </h1>
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            Hey {first_name}, your co-founder has requested to dissolve your partnership. 
            This doesn't happen immediately — you have time to talk things through.
        </p>
        
        {reason_block}
        
        <div style="background: #f1f5f9; border-radius: 8px; padding: 20px; margin: 0 0 24px;">
            <h3 style="margin: 0 0 12px; font-size: 16px; font-weight: 600; color: #0f172a;">
                What happens next?
            </h3>
            <ul style="margin: 0; padding-left: 20px; color: #475569; line-height: 1.8;">
                <li>You have until <strong>{end_date}</strong> to respond</li>
                <li>You can <strong>confirm</strong> to end the partnership immediately</li>
                <li>Or wait — the partnership will automatically end on {end_date}</li>
                <li>Your workspace data will be <strong>preserved</strong> in read-only mode</li>
            </ul>
        </div>
        
        <p style="margin: 0 0 24px; font-size: 16px; color: #475569; line-height: 1.6;">
            We encourage you to have a conversation with {requester_name} before deciding. 
            Many partnerships can be saved with open communication.
        </p>
        
        <table role="presentation" cellspacing="0" cellpadding="0" border="0">
            <tr>
                <td style="border-radius: 8px; background-color: #0d9488;">
                    <a href="{FRONTEND_URL}/workspaces/{workspace_id}" 
                       style="display: inline-block; padding: 14px 28px; font-size: 16px; font-weight: 600; color: #ffffff; text-decoration: none;">
                        View Workspace →
                    </a>
                </td>
            </tr>
        </table>
        
        <p style="margin: 24px 0 0; font-size: 14px; color: #94a3b8;">
            Need help mediating? Reply to this email and we can connect you with an advisor.
        </p>
    '''
    
    return send_email(
        to_email=to_email,
        subject=f"⚠️ {requester_name} has requested to end your partnership",
        html_body=_base_template(content, "Partnership dissolution request")
    )
