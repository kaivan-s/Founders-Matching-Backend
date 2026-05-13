"""
Application Service - Handles project owner's application management.

This service powers the "I have a project" flow where owners:
1. Receive applications from seekers
2. Review applicant profiles
3. Accept or reject applications
4. Create matches when accepting
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from config.database import get_supabase
from utils.logger import log_info, log_error


def get_applications_for_owner(clerk_user_id: str) -> Dict[str, Any]:
    """
    Get all applications for projects owned by the current user.
    
    Returns dict with applications grouped by project.
    """
    supabase = get_supabase()
    
    # Get owner's founder profile
    owner = supabase.table('founders').select('id').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not owner.data:
        raise ValueError("Profile not found")
    
    owner_id = owner.data[0]['id']
    
    # Get all applications for owner's projects with full applicant info
    applications = supabase.table('applications').select(
        '''*, 
        applicant:founders!applicant_id(
            id, name, email, profile_picture_url, headline, bio, 
            location, skills, linkedin_url, linkedin_verified, github_verified,
            interests, expertise_details, work_preferences, looking_for_description
        ),
        project:projects!project_id(id, title, stage, genre)'''
    ).eq('project_owner_id', owner_id).order('created_at', desc=True).execute()
    
    if not applications.data:
        return {'applications': [], 'pending_count': 0, 'by_project': {}}
    
    # Group applications by project and count pending
    by_project = {}
    pending_count = 0
    
    for app in applications.data:
        project = app.get('project') or {}
        project_id = project.get('id')
        
        if project_id not in by_project:
            by_project[project_id] = {
                'project': project,
                'applications': [],
                'pending_count': 0,
            }
        
        applicant = app.get('applicant') or {}
        verification = _compute_verification(applicant)
        
        formatted_app = {
            'id': app['id'],
            'status': app['status'],
            'created_at': app['created_at'],
            'responded_at': app.get('responded_at'),
            'interest_reason': app.get('interest_reason'),
            'value_proposition': app.get('value_proposition'),
            'question_answers': app.get('question_answers', {}),
            'video_intro_url': app.get('video_intro_url'),
            'voice_intro_url': app.get('voice_intro_url'),
            'applicant': {
                'id': applicant.get('id'),
                'name': applicant.get('name'),
                'email': applicant.get('email'),
                'profile_picture_url': applicant.get('profile_picture_url'),
                'headline': applicant.get('headline'),
                'bio': applicant.get('bio'),
                'location': applicant.get('location'),
                'skills': applicant.get('skills', []),
                'linkedin_url': applicant.get('linkedin_url'),
                'interests': applicant.get('interests', []),
                'work_preferences': applicant.get('work_preferences', {}),
                'looking_for_description': applicant.get('looking_for_description'),
                'verification': verification,
            }
        }
        
        by_project[project_id]['applications'].append(formatted_app)
        
        if app['status'] == 'pending':
            by_project[project_id]['pending_count'] += 1
            pending_count += 1
    
    # Flatten for simple list view
    all_applications = []
    for project_data in by_project.values():
        for app in project_data['applications']:
            app['project'] = project_data['project']
            all_applications.append(app)
    
    return {
        'applications': all_applications,
        'pending_count': pending_count,
        'by_project': by_project,
    }


def get_application_detail(clerk_user_id: str, application_id: str) -> Dict[str, Any]:
    """
    Get detailed view of a single application.
    """
    supabase = get_supabase()
    
    # Get owner's founder profile
    owner = supabase.table('founders').select('id').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not owner.data:
        raise ValueError("Profile not found")
    
    owner_id = owner.data[0]['id']
    
    # Get application with full details
    application = supabase.table('applications').select(
        '''*, 
        applicant:founders!applicant_id(*),
        project:projects!project_id(id, title, description, stage, genre, application_questions)'''
    ).eq('id', application_id).eq('project_owner_id', owner_id).execute()
    
    if not application.data:
        raise ValueError("Application not found")
    
    app = application.data[0]
    applicant = app.get('applicant') or {}
    project = app.get('project') or {}
    
    # Get applicant's projects for context
    applicant_projects = supabase.table('projects').select(
        'id, title, description, stage, genre'
    ).eq('founder_id', applicant.get('id')).eq('is_active', True).limit(5).execute()
    
    verification = _compute_verification(applicant)
    
    return {
        'id': app['id'],
        'status': app['status'],
        'created_at': app['created_at'],
        'responded_at': app.get('responded_at'),
        'interest_reason': app.get('interest_reason'),
        'value_proposition': app.get('value_proposition'),
        'question_answers': app.get('question_answers', {}),
        'video_intro_url': app.get('video_intro_url'),
        'voice_intro_url': app.get('voice_intro_url'),
        'project': project,
        'applicant': {
            'id': applicant.get('id'),
            'name': applicant.get('name'),
            'email': applicant.get('email'),
            'profile_picture_url': applicant.get('profile_picture_url'),
            'headline': applicant.get('headline'),
            'bio': applicant.get('bio'),
            'location': applicant.get('location'),
            'skills': applicant.get('skills', []),
            'linkedin_url': applicant.get('linkedin_url'),
            'website_url': applicant.get('website_url'),
            'twitter_url': applicant.get('twitter_url'),
            'github_url': applicant.get('github_url'),
            'interests': applicant.get('interests', []),
            'expertise_details': applicant.get('expertise_details'),
            'work_preferences': applicant.get('work_preferences', {}),
            'looking_for_description': applicant.get('looking_for_description'),
            'past_projects': applicant.get('past_projects'),
            'verification': verification,
            'projects': applicant_projects.data if applicant_projects.data else [],
        }
    }


def respond_to_application(
    clerk_user_id: str, 
    application_id: str, 
    response: str,
    rejection_reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Accept or reject an application.
    
    Args:
        clerk_user_id: Owner's clerk user ID
        application_id: The application to respond to
        response: 'accept' or 'reject'
        rejection_reason: Optional feedback for rejection
    
    Returns:
        Result dict with match info if accepted
    """
    if response not in ['accept', 'reject']:
        raise ValueError("Response must be 'accept' or 'reject'")
    
    supabase = get_supabase()
    
    # Get owner's founder profile
    owner = supabase.table('founders').select('id, name, email').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not owner.data:
        raise ValueError("Profile not found")
    
    owner_id = owner.data[0]['id']
    owner_name = owner.data[0].get('name')
    owner_email = owner.data[0].get('email')
    
    # Get application and verify ownership
    application = supabase.table('applications').select(
        '''*, 
        applicant:founders!applicant_id(id, name, email),
        project:projects!project_id(id, title, seeking_cofounder)'''
    ).eq('id', application_id).eq('project_owner_id', owner_id).execute()
    
    if not application.data:
        raise ValueError("Application not found")
    
    app = application.data[0]
    
    if app['status'] != 'pending':
        raise ValueError(f"Application already {app['status']}")
    
    applicant = app.get('applicant') or {}
    project = app.get('project') or {}
    applicant_id = applicant.get('id')
    project_id = project.get('id')
    
    if not project.get('seeking_cofounder'):
        raise ValueError("This project is no longer seeking a co-founder")
    
    now = datetime.now(timezone.utc).isoformat()
    
    if response == 'accept':
        # Create match
        match_data = {
            'founder1_id': min(owner_id, applicant_id),
            'founder2_id': max(owner_id, applicant_id),
            'project_id': project_id,
        }
        
        # Check if match already exists
        existing_match = supabase.table('matches').select('id').eq(
            'founder1_id', match_data['founder1_id']
        ).eq('founder2_id', match_data['founder2_id']).eq(
            'project_id', project_id
        ).execute()
        
        if existing_match.data:
            match_id = existing_match.data[0]['id']
        else:
            match_result = supabase.table('matches').insert(match_data).execute()
            if not match_result.data:
                raise ValueError("Failed to create match")
            match_id = match_result.data[0]['id']
            
            # Calculate compatibility score
            try:
                from services.compatibility_service import save_compatibility_score
                save_compatibility_score(match_id, owner_id, applicant_id, project_id)
            except Exception as e:
                log_error(f"Failed to calculate compatibility score", error=e)
        
        # Create workspace
        try:
            from services.workspace_service import create_workspace_for_match
            workspace_id = create_workspace_for_match(match_id)
        except Exception as e:
            log_error(f"Failed to create workspace", error=e)
            # Rollback match
            supabase.table('matches').delete().eq('id', match_id).execute()
            raise ValueError("Failed to create workspace for match")
        
        # Mark project as no longer seeking
        supabase.table('projects').update({
            'seeking_cofounder': False
        }).eq('id', project_id).execute()
        
        # Update application status
        supabase.table('applications').update({
            'status': 'accepted',
            'responded_at': now,
        }).eq('id', application_id).execute()
        
        # Reject other pending applications for this project
        supabase.table('applications').update({
            'status': 'rejected',
            'responded_at': now,
            'rejection_reason': 'Position has been filled',
        }).eq('project_id', project_id).eq('status', 'pending').execute()
        
        # Record activation milestones
        try:
            from services import activation_service
            for fid in (owner_id, applicant_id):
                activation_service.record_milestone(
                    fid, activation_service.Milestone.FIRST_MATCH,
                    {'match_id': match_id, 'project_id': project_id},
                )
        except Exception:
            pass
        
        # Send notifications
        _notify_application_accepted(
            applicant_id=applicant_id,
            applicant_email=applicant.get('email'),
            applicant_name=applicant.get('name'),
            owner_name=owner_name,
            project_title=project.get('title'),
            workspace_id=workspace_id,
        )
        
        return {
            "message": "Application accepted! Match created.",
            "match_id": match_id,
            "workspace_id": workspace_id,
        }
    
    else:  # reject
        supabase.table('applications').update({
            'status': 'rejected',
            'responded_at': now,
            'rejection_reason': (rejection_reason or '').strip()[:500] or None,
        }).eq('id', application_id).execute()
        
        # Send notification
        _notify_application_rejected(
            applicant_id=applicant_id,
            applicant_email=applicant.get('email'),
            applicant_name=applicant.get('name'),
            project_title=project.get('title'),
        )
        
        return {"message": "Application rejected"}


def get_application_stats(clerk_user_id: str) -> Dict[str, Any]:
    """
    Get application statistics for project owner dashboard.
    """
    supabase = get_supabase()
    
    # Get owner's founder profile
    owner = supabase.table('founders').select('id').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not owner.data:
        raise ValueError("Profile not found")
    
    owner_id = owner.data[0]['id']
    
    # Get counts by status
    all_apps = supabase.table('applications').select('status').eq(
        'project_owner_id', owner_id
    ).execute()
    
    stats = {
        'total': 0,
        'pending': 0,
        'accepted': 0,
        'rejected': 0,
        'withdrawn': 0,
    }
    
    for app in (all_apps.data or []):
        stats['total'] += 1
        status = app.get('status', 'pending')
        if status in stats:
            stats[status] += 1
    
    return stats


# ============================================
# PRIVATE HELPER FUNCTIONS
# ============================================

def _compute_verification(founder: Dict) -> Dict:
    """Compute verification summary."""
    linkedin = founder.get('linkedin_verified', False)
    github = founder.get('github_verified', False)
    
    if linkedin and github:
        return {'tier': 'HIGHLY_VERIFIED', 'label': 'Highly Verified', 
                'linkedin': True, 'github': True}
    elif linkedin or github:
        return {'tier': 'VERIFIED', 'label': 'Verified',
                'linkedin': linkedin, 'github': github}
    else:
        return {'tier': 'UNVERIFIED', 'label': 'Not Verified',
                'linkedin': False, 'github': False}


def _notify_application_accepted(
    applicant_id: str,
    applicant_email: str,
    applicant_name: str,
    owner_name: str,
    project_title: str,
    workspace_id: str,
) -> None:
    """Notify applicant their application was accepted."""
    supabase = get_supabase()
    
    # In-app notification (wrapped in try/catch so email still sends if this fails)
    try:
        supabase.table('notifications').insert({
            'user_id': applicant_id,
            'type': 'application_accepted',
            'title': f"You're in! 🎉",
            'message': f"{owner_name} accepted your application to join {project_title}",
            'data': {
                'workspace_id': workspace_id,
                'project_title': project_title,
            }
        }).execute()
    except Exception as e:
        print(f"[NOTIFY] In-app notification insert failed: {e}")
    
    # Email notification
    print(f"[NOTIFY] _notify_application_accepted: applicant_email={applicant_email}")
    if applicant_email:
        try:
            from services import email_service
            email_service.send_new_match_email(
                to_email=applicant_email,
                user_name=applicant_name or 'there',
                partner_name=owner_name,
                partner_project=project_title,
                workspace_id=workspace_id,
            )
        except Exception as e:
            print(f"[NOTIFY] EXCEPTION in send_new_match_email: {e}")
            log_error("Failed to send acceptance email", error=e)
    else:
        print("[NOTIFY] SKIP: No applicant_email provided")


def _notify_application_rejected(
    applicant_id: str,
    applicant_email: str,
    applicant_name: str,
    project_title: str,
) -> None:
    """Notify applicant their application was not accepted."""
    supabase = get_supabase()
    
    # In-app notification (wrapped in try/catch)
    try:
        supabase.table('notifications').insert({
            'user_id': applicant_id,
            'type': 'application_rejected',
            'title': f"Application update",
            'message': f"Your application to {project_title} wasn't selected this time",
            'data': {
                'project_title': project_title,
            }
        }).execute()
    except Exception as e:
        print(f"[NOTIFY] In-app notification insert failed: {e}")
