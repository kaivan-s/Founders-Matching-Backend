"""
Seeker Service - Handles the "I want to join a project" flow.

This service powers the questionnaire-based discovery where seekers:
1. Fill out a short questionnaire about what they're looking for
2. Get curated project matches based on their answers
3. Apply to projects they're interested in
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from typing import Dict, List, Optional, Any, Tuple

from config.database import get_supabase
from utils.logger import log_info, log_error


# Questionnaire options (used for validation and scoring)
ROLE_OPTIONS = ['technical', 'business', 'product', 'generalist']
STAGE_OPTIONS = ['idea', 'mvp', 'early_revenue', 'scaling', 'any']
INDUSTRY_OPTIONS = [
    'fintech', 'healthcare', 'ai_ml', 'b2b_saas', 'consumer', 
    'climate', 'education', 'ecommerce', 'gaming', 'other'
]
AVAILABILITY_OPTIONS = ['full_time', 'part_time_to_full', 'part_time_only', 'exploring']
PRIORITY_OPTIONS = [
    'equity_upside', 'learning', 'strong_technical', 
    'revenue_traction', 'mission_impact', 'location_flexibility'
]
DEALBREAKER_OPTIONS = ['remote_friendly', 'has_funding', 'founder_verified', 'none']

DAILY_DISCOVERY_BATCH_SIZE = 3
SEEKER_DISCOVERY_FEED_TABLE = 'seeker_discovery_daily_feed'


def _preference_key(validated: Dict[str, Any]) -> str:
    blob = json.dumps(validated, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _next_utc_midnight() -> datetime:
    now = datetime.now(timezone.utc)
    tomorrow = now.date() + timedelta(days=1)
    return datetime(
        tomorrow.year, tomorrow.month, tomorrow.day,
        tzinfo=timezone.utc,
    )


def _get_projects_with_insights(supabase, project_ids: List[str]) -> set:
    """Get set of project IDs that have completed insights."""
    if not project_ids:
        return set()
    
    try:
        result = supabase.table('project_insights').select('project_id').in_(
            'project_id', project_ids
        ).eq('status', 'completed').execute()
        
        return {row['project_id'] for row in (result.data or [])}
    except Exception as e:
        log_error("Failed to get projects with insights", error=e)
        return set()


def _rank_discovery_candidates(
    supabase,
    seeker_id: str,
    seeker_skills: set,
    validated: Dict[str, Any],
    extra_exclude_ids: Optional[set] = None,
    visible_tiers: Optional[List[str]] = None,
    include_skipped: bool = False,
) -> List[Dict[str, Any]]:
    """Return scored project rows [{project, score, match_reasons}, ...] descending by score.
    
    Args:
        visible_tiers: No longer used for filtering - all projects are visible.
                      Instead, PRO/PRO+ founders get a ranking boost.
        include_skipped: If True, include previously skipped projects (for Pro/Pro+ "see skipped" feature)
    """
    extra_exclude_ids = extra_exclude_ids or set()
    
    # Include founder's plan for ranking boost (not filtering)
    query = supabase.table('projects').select(
        '*, founder:founders!founder_id(id, name, profile_picture_url, location, '
        'linkedin_verified, github_verified, linkedin_url, skills, headline, plan)'
    ).eq('is_active', True).eq('seeking_cofounder', True).neq('founder_id', seeker_id)
    
    result = query.order('created_at', desc=True).limit(240).execute()
    if not result.data:
        return []
    
    existing_applications = supabase.table('applications').select('project_id').eq(
        'applicant_id', seeker_id
    ).execute()
    applied_project_ids = {app['project_id'] for app in (existing_applications.data or [])}
    
    # FIX #2: Only exclude projects that are no longer seeking (not all matched projects)
    # Projects with seeking_cofounder=False are already filtered above
    # No need to exclude all matched projects globally
    
    # Get skipped projects (left swipes)
    skipped_project_ids = set()
    if not include_skipped:
        skipped_projects = supabase.table('swipes').select('project_id').eq(
            'swiper_id', seeker_id
        ).eq('swipe_type', 'left').execute()
        skipped_project_ids = {s['project_id'] for s in (skipped_projects.data or []) if s.get('project_id')}
    
    scored_projects: List[Dict[str, Any]] = []
    
    for project in result.data:
        project_id = project['id']
        if project_id in applied_project_ids:
            continue
        if project_id in skipped_project_ids:
            continue
        if project_id in extra_exclude_ids:
            continue
        
        # FIX #1: No tier filtering - everyone sees all projects
        # Instead, give PRO/PRO+ founders a ranking boost
        founder = project.get('founder') or {}
        founder_plan = founder.get('plan', 'FREE')
        
        if not _passes_dealbreakers(project, validated['dealbreakers']):
            continue
        score, match_reasons = _calculate_match_score(project, validated, seeker_skills)
        
        # Ranking boost for PRO/PRO+ founders (verified/serious founders)
        if founder_plan == 'PRO_PLUS':
            score += 15  # Strong boost for PRO+ founders
            match_reasons.append('Pro+ founder')
        elif founder_plan == 'PRO':
            score += 8  # Moderate boost for PRO founders
            match_reasons.append('Pro founder')
        
        if score > 0:
            scored_projects.append({
                'project': project,
                'score': score,
                'match_reasons': match_reasons,
            })
    
    scored_projects.sort(key=lambda x: x['score'], reverse=True)
    return scored_projects


def _historical_discovery_ids(supabase, seeker_id: str, before_date: str) -> set:
    try:
        res = (
            supabase.table(SEEKER_DISCOVERY_FEED_TABLE)
            .select('project_ids')
            .eq('seeker_id', seeker_id)
            .lt('feed_date', before_date)
            .execute()
        )
    except Exception as e:
        log_error(f"Historical discovery IDs lookup failed ({SEEKER_DISCOVERY_FEED_TABLE})", error=e)
        return set()
    out: set = set()
    for row in res.data or []:
        ids = row.get('project_ids') or []
        for pid in ids:
            out.add(pid)
    return out


def _send_discovery_daily_digest_email(seeker_record: Dict[str, Any]) -> bool:
    """Transactional email prompting the seeker to open Discover."""
    email = (seeker_record.get('email') or '').strip()
    if not email:
        return False
    try:
        from services import email_service
        ok = email_service.send_discovery_daily_matches_ready_email(
            to_email=email,
            user_name=seeker_record.get('name') or 'there',
        )
        return bool(ok)
    except Exception as e:
        log_error("Discovery digest email failed", error=e)
        return False


def _format_discovery_match(score: int, match_reasons: List[str], project: Dict[str, Any], has_insights: bool = False) -> Dict[str, Any]:
    founder = project.get('founder') or {}
    verification = _compute_verification_info(founder)
    return {
        'id': project['id'],
        'title': project.get('title', 'Untitled Project'),
        'description': project.get('description', ''),
        'stage': project.get('stage'),
        'genre': project.get('genre'),
        'needed_skills': project.get('needed_skills', []),
        'compatibility_answers': project.get('compatibility_answers', {}),
        'application_questions': project.get('application_questions', []),
        'created_at': project.get('created_at'),
        'founder': {
            'id': founder.get('id'),
            'name': founder.get('name'),
            'profile_picture_url': founder.get('profile_picture_url'),
            'location': founder.get('location'),
            'headline': founder.get('headline'),
            'skills': founder.get('skills', []),
            'linkedin_url': founder.get('linkedin_url'),
            'verification': verification,
        },
        'match_score': score,
        'match_reasons': match_reasons,
        'has_insights': has_insights,
    }


def _build_matches_from_ordered_ids(
    ordered_ids: List[str],
    ranked_by_id: Dict[str, Dict[str, Any]],
    ranked_order: List[Dict[str, Any]],
    *,
    validated: Dict[str, Any],
    seeker_skills: set,
    seeker_id: str,
    applied_project_ids: set,
    matched_project_ids: set,
    batch_cap: int,
    projects_with_insights: Optional[set] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Hydrate formatted matches preserving order where possible.
    Top up from ranked_order if stored ids are no longer available.
    """
    formatted: List[Dict[str, Any]] = []
    used: set = set()
    projects_with_insights = projects_with_insights or set()
    
    def _eligible_live(project: Dict) -> bool:
        pid = project['id']
        if pid in applied_project_ids or pid in matched_project_ids:
            return False
        if str(project.get('founder_id', '')) == str(seeker_id):
            return False
        return bool(project.get('is_active')) and bool(project.get('seeking_cofounder'))
    
    def _append_project(project_dict: Dict[str, Any]) -> bool:
        if not project_dict:
            return False
        if not _eligible_live(project_dict):
            return False
        if not _passes_dealbreakers(project_dict, validated['dealbreakers']):
            return False
        score, reasons = _calculate_match_score(project_dict, validated, seeker_skills)
        if score <= 0:
            return False
        has_insights = project_dict['id'] in projects_with_insights
        formatted.append(_format_discovery_match(score, reasons, project_dict, has_insights=has_insights))
        used.add(project_dict['id'])
        return True
    
    def _append_from_row(match_row: Optional[Dict[str, Any]]) -> bool:
        return _append_project(match_row['project']) if match_row else False
    
    for pid in ordered_ids:
        if len(formatted) >= batch_cap:
            break
        if pid in ranked_by_id:
            _append_from_row(ranked_by_id[pid])
    
    if len(formatted) < batch_cap:
        for row in ranked_order:
            if len(formatted) >= batch_cap:
                break
            proj_id = row['project']['id']
            if proj_id in used:
                continue
            _append_from_row(row)
    
    final_ids = [m['id'] for m in formatted]
    return formatted, final_ids


def get_skipped_projects(
    clerk_user_id: str,
    questionnaire: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Get previously skipped projects for Pro/Pro+ users.
    Allows users to revisit projects they may have accidentally skipped.
    
    Returns { "matches": [...], "can_view_skipped": bool }.
    """
    from services import plan_service
    
    supabase = get_supabase()
    
    # Check plan - only Pro/Pro+ can view skipped
    plan_config = plan_service.get_founder_plan(clerk_user_id)
    plan_id = plan_config.get('id', 'FREE')
    
    if plan_id == 'FREE':
        return {
            'matches': [],
            'can_view_skipped': False,
            'message': 'Upgrade to Pro to view skipped projects'
        }
    
    seeker = supabase.table('founders').select('id, skills').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not seeker.data:
        return {'matches': [], 'can_view_skipped': True}
    
    seeker_id = seeker.data[0]['id']
    seeker_skills = set(seeker.data[0].get('skills') or [])
    validated = _validate_questionnaire(questionnaire)
    
    # Get skipped projects with include_skipped=True to re-rank them
    ranked = _rank_discovery_candidates(
        supabase, seeker_id, seeker_skills, validated,
        include_skipped=True,
    )
    
    # Filter to only skipped ones
    skipped_projects = supabase.table('swipes').select('project_id').eq(
        'swiper_id', seeker_id
    ).eq('swipe_type', 'left').execute()
    skipped_ids = {s['project_id'] for s in (skipped_projects.data or [])}
    
    # Get projects that will be returned
    skipped_project_ids = [row['project']['id'] for row in ranked if row['project']['id'] in skipped_ids][:20]
    
    # Get insights status for skipped projects
    projects_with_insights = _get_projects_with_insights(supabase, skipped_project_ids)
    
    # Only return projects that were skipped
    skipped_matches = []
    for row in ranked:
        project_id = row['project']['id']
        if project_id in skipped_ids:
            has_insights = project_id in projects_with_insights
            skipped_matches.append(
                _format_discovery_match(row['score'], row['match_reasons'], row['project'], has_insights=has_insights)
            )
    
    return {
        'matches': skipped_matches[:20],  # Limit to 20
        'can_view_skipped': True,
        'total_skipped': len(skipped_ids),
    }


def search_projects_for_seeker(
    clerk_user_id: str,
    questionnaire: Dict[str, Any],
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Find matching projects for seeker.
    
    - FREE users: Results cached for 24 hours. Can only get fresh results once per day.
    - PRO/PRO+ users: Always fresh results, can change preferences anytime.
    
    Returns { "matches": [...], "discovery": meta }.
    """
    from services import plan_service
    
    supabase = get_supabase()
    
    seeker = supabase.table('founders').select('id, skills, email, name').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not seeker.data:
        raise ValueError("Profile not found. Please create your profile first.")
    
    seeker_rec = seeker.data[0]
    seeker_id = seeker_rec['id']
    seeker_skills = set(seeker_rec.get('skills') or [])
    
    # Get user's plan info
    plan_config = plan_service.get_founder_plan(clerk_user_id)
    user_plan = plan_config.get('id', 'FREE')
    discovery_config = plan_config.get('discovery', {})
    
    validated = _validate_questionnaire(questionnaire)
    
    # Get plan-based project visibility limit (FREE=5, PRO=25, PRO_PLUS=50)
    max_visible = discovery_config.get('maxProjectsVisible', 5)
    batch_cap = limit if limit and limit < max_visible else max_visible
    
    # For FREE users, check if they already searched today - return cached results
    if user_plan == 'FREE':
        cached = _get_cached_daily_results(supabase, seeker_id)
        if cached:
            # Return cached results with fresh scores based on current preferences
            ranked = _rank_discovery_candidates(supabase, seeker_id, seeker_skills, validated)
            return _return_cached_results_with_scores(
                supabase, cached['project_ids'], max_visible, user_plan, ranked, clerk_user_id
            )
    
    # Get ALL ranked candidates (fresh search)
    ranked = _rank_discovery_candidates(
        supabase, seeker_id, seeker_skills, validated,
    )
    
    # Get insights status for projects in batch
    project_ids_for_insights = [row['project']['id'] for row in ranked[:batch_cap]]
    projects_with_insights = _get_projects_with_insights(supabase, project_ids_for_insights)
    
    # Format results
    formatted: List[Dict[str, Any]] = []
    for row in ranked[:batch_cap]:
        has_insights = row['project']['id'] in projects_with_insights
        formatted.append(_format_project_match(row, has_insights=has_insights))
    
    # For FREE users, cache today's results
    is_free_user = user_plan == 'FREE'
    if is_free_user:
        project_ids = [f['id'] for f in formatted]
        _cache_daily_results(supabase, seeker_id, project_ids)
    
    # Get remaining applications for the day
    can_apply, apps_sent_today, max_apps = plan_service.check_connect_limit(clerk_user_id)
    apps_remaining = max(0, max_apps - apps_sent_today) if max_apps != -1 else -1  # -1 means unlimited
    
    discovery_meta: Dict[str, Any] = {
        'total_available': len(ranked),
        'returned_count': len(formatted),
        'max_visible': max_visible,
        'user_plan': user_plan,
        'has_more': len(ranked) > len(formatted),
        'results_cached': is_free_user,  # FREE users can't edit after first search
        'applications_remaining': apps_remaining,
        'applications_sent_today': apps_sent_today,
        'max_applications_per_day': max_apps,
    }
    
    _save_search_history(seeker_id, validated, len(formatted))
    return {'matches': formatted, 'discovery': discovery_meta}


def _format_project_match(row: Dict, has_insights: bool = False) -> Dict[str, Any]:
    """Format a ranked project row into the match response format."""
    project = row['project']
    founder = project.get('founder') or {}
    verification = _compute_verification_info(founder)
    founder_plan = founder.get('plan', 'FREE')
    
    return {
        'id': project['id'],
        'title': project.get('title', 'Untitled Project'),
        'description': project.get('description', ''),
        'stage': project.get('stage'),
        'genre': project.get('genre'),
        'needed_skills': project.get('needed_skills', []),
        'compatibility_answers': project.get('compatibility_answers', {}),
        'application_questions': project.get('application_questions', []),
        'created_at': project.get('created_at'),
        'founder': {
            'id': founder.get('id'),
            'name': founder.get('name'),
            'profile_picture_url': founder.get('profile_picture_url'),
            'location': founder.get('location'),
            'headline': founder.get('headline'),
            'skills': founder.get('skills', []),
            'linkedin_url': founder.get('linkedin_url'),
            'verification': verification,
            'plan': founder_plan,
        },
        'match_score': row['score'],
        'match_reasons': row['match_reasons'],
        'has_insights': has_insights,
    }


def _get_cached_daily_results(supabase, seeker_id: str) -> Optional[Dict]:
    """Check if FREE user has cached results for today."""
    from datetime import datetime, timezone
    
    today = datetime.now(timezone.utc).date().isoformat()
    
    try:
        result = supabase.table('seeker_daily_views').select('project_ids').eq(
            'seeker_id', seeker_id
        ).eq('view_date', today).execute()
        
        if result.data and result.data[0].get('project_ids'):
            return {'project_ids': result.data[0]['project_ids']}
        return None
    except Exception:
        return None


def _cache_daily_results(supabase, seeker_id: str, project_ids: List[str]) -> None:
    """Cache today's search results for FREE user."""
    from datetime import datetime, timezone
    
    if not project_ids:
        return
    
    today = datetime.now(timezone.utc).date().isoformat()
    
    try:
        supabase.table('seeker_daily_views').upsert({
            'seeker_id': seeker_id,
            'view_date': today,
            'project_ids': project_ids,
        }, on_conflict='seeker_id,view_date').execute()
    except Exception as e:
        log_error("Failed to cache daily results", error=e)


def _return_cached_results_with_scores(
    supabase, project_ids: List[str], max_visible: int, user_plan: str, ranked: List[Dict], clerk_user_id: str
) -> Dict[str, Any]:
    """Return cached results with fresh scores from current preferences."""
    from services import plan_service
    
    # Get remaining applications for the day
    can_apply, apps_sent_today, max_apps = plan_service.check_connect_limit(clerk_user_id)
    apps_remaining = max(0, max_apps - apps_sent_today) if max_apps != -1 else -1
    
    if not project_ids:
        return {'matches': [], 'discovery': {
            'total_available': 0,
            'returned_count': 0,
            'max_visible': max_visible,
            'user_plan': user_plan,
            'has_more': False,
            'results_cached': True,
            'applications_remaining': apps_remaining,
            'applications_sent_today': apps_sent_today,
            'max_applications_per_day': max_apps,
        }}
    
    cached_set = set(project_ids[:max_visible])
    
    # Get insights status for all cached projects
    projects_with_insights = _get_projects_with_insights(supabase, project_ids[:max_visible])
    
    # Get cached projects from ranked list (they'll have proper scores)
    formatted = []
    for row in ranked:
        project_id = row['project']['id']
        if project_id in cached_set:
            has_insights = project_id in projects_with_insights
            formatted.append(_format_project_match(row, has_insights=has_insights))
            cached_set.discard(project_id)
    
    # For any cached projects not in ranked (e.g., became inactive or stopped seeking), fetch from DB
    if cached_set:
        projects_result = supabase.table('projects').select(
            '*, founders!projects_founder_id_fkey(*)'
        ).in_('id', list(cached_set)).eq('is_active', True).eq('seeking_cofounder', True).execute()
        
        for project in (projects_result.data or []):
            founder = project.get('founders') or {}
            verification = _compute_verification_info(founder)
            has_insights = project['id'] in projects_with_insights
            formatted.append({
                'id': project['id'],
                'title': project.get('title', 'Untitled Project'),
                'description': project.get('description', ''),
                'stage': project.get('stage'),
                'genre': project.get('genre'),
                'needed_skills': project.get('needed_skills', []),
                'compatibility_answers': project.get('compatibility_answers', {}),
                'application_questions': project.get('application_questions', []),
                'created_at': project.get('created_at'),
                'founder': {
                    'id': founder.get('id'),
                    'name': founder.get('name'),
                    'profile_picture_url': founder.get('profile_picture_url'),
                    'location': founder.get('location'),
                    'headline': founder.get('headline'),
                    'skills': founder.get('skills', []),
                    'linkedin_url': founder.get('linkedin_url'),
                    'verification': verification,
                    'plan': founder.get('plan', 'FREE'),
                },
                'match_score': 0,
                'match_reasons': [],
                'has_insights': has_insights,
            })
    
    # Sort by score descending
    formatted.sort(key=lambda x: x.get('match_score', 0), reverse=True)
    
    return {
        'matches': formatted,
        'discovery': {
            'total_available': len(ranked),
            'returned_count': len(formatted),
            'max_visible': max_visible,
            'user_plan': user_plan,
            'has_more': len(ranked) > len(formatted),
            'results_cached': True,
            'applications_remaining': apps_remaining,
            'applications_sent_today': apps_sent_today,
            'max_applications_per_day': max_apps,
        }
    }


def _looks_like_saved_discovery_prefs(compatibility_answers: Any) -> bool:
    if not isinstance(compatibility_answers, dict) or len(compatibility_answers) == 0:
        return False
    keys = {'role', 'stage', 'industries', 'availability', 'priorities', 'dealbreakers'}
    return bool(keys.intersection(compatibility_answers.keys()))


def _mark_discovery_digest_sent(supabase, seeker_id: str, feed_date: str) -> None:
    """Prevent duplicate automated digest emails for this UTC day."""
    try:
        supabase.table(SEEKER_DISCOVERY_FEED_TABLE).update({
            'digest_email_sent': True,
        }).eq('seeker_id', seeker_id).eq('feed_date', feed_date).execute()
    except Exception as e:
        log_error('Failed to set digest_email_sent', error=e)


def run_discovery_daily_digest_cron() -> Dict[str, Any]:
    """
    Daily campaign email cron job.
    
    Sends emails to a rotating batch of users (25% per day = 4-day cycle).
    Users are randomly selected from those not emailed in the last 4 days.
    
    This ensures:
    - Random fair distribution across users
    - No user gets spammed (4-day cooldown)
    - Scales automatically as user base grows
    """
    from services.email_service import send_campaign_discovery_email
    from datetime import datetime, timezone
    
    supabase = get_supabase()
    
    BATCH_PERCENTAGE = 0.25  # 25% of users per day = 4-day cycle
    
    stats = {
        'ok': True,
        'total_eligible_users': 0,
        'batch_size': 0,
        'emails_sent': 0,
        'emails_failed': 0,
        'skipped_no_email': 0,
    }
    
    try:
        # Get count of eligible users (active, not deleted, not unsubscribed)
        # Note: email_unsubscribed might not exist yet, handle gracefully
        try:
            count_query = supabase.table('founders').select('id', count='exact').eq(
                'is_active', True
            ).eq('is_deleted', False).eq('email_unsubscribed', False).execute()
        except Exception:
            # Column might not exist yet, fallback without unsubscribe filter
            count_query = supabase.table('founders').select('id', count='exact').eq(
                'is_active', True
            ).eq('is_deleted', False).execute()
        
        total_users = count_query.count or 0
        stats['total_eligible_users'] = total_users
        
        if total_users == 0:
            stats['message'] = 'No eligible users found'
            return stats
        
        # Calculate batch size (25% of total, minimum 1)
        batch_size = max(1, int(total_users * BATCH_PERCENTAGE))
        stats['batch_size'] = batch_size
        
        # Get users to email: random selection from those not emailed in last 4 days
        import random
        from datetime import timedelta
        
        four_days_ago = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        
        try:
            # Get all eligible users (not emailed in last 4 days or never emailed)
            users_query = supabase.table('founders').select(
                'id, email, name'
            ).eq('is_active', True).eq('is_deleted', False).eq(
                'email_unsubscribed', False
            ).or_(
                f'last_campaign_email_at.is.null,last_campaign_email_at.lt.{four_days_ago}'
            ).execute()
        except Exception:
            # Fallback without unsubscribe/timestamp filter
            users_query = supabase.table('founders').select(
                'id, email, name'
            ).eq('is_active', True).eq('is_deleted', False).execute()
        
        all_eligible = users_query.data or []
        
        # Randomly select batch_size users
        if len(all_eligible) > batch_size:
            users_to_email = random.sample(all_eligible, batch_size)
        else:
            users_to_email = all_eligible
        
        if not users_to_email:
            stats['message'] = 'No users in batch'
            return stats
        
        # Send emails to each user in the batch
        now = datetime.now(timezone.utc).isoformat()
        
        for user in users_to_email:
            user_email = user.get('email')
            user_name = user.get('name')
            user_id = user.get('id')
            
            if not user_email:
                stats['skipped_no_email'] += 1
                continue
            
            # Send the campaign email
            success = send_campaign_discovery_email(
                to_email=user_email,
                user_name=user_name,
            )
            
            if success:
                stats['emails_sent'] += 1
                # Update last_campaign_email_at
                try:
                    supabase.table('founders').update({
                        'last_campaign_email_at': now
                    }).eq('id', user_id).execute()
                except Exception as e:
                    log_error(f"Failed to update last_campaign_email_at for {user_id}", error=e)
            else:
                stats['emails_failed'] += 1
        
        stats['message'] = f"Sent {stats['emails_sent']} campaign emails"
        return stats
        
    except Exception as e:
        log_error("Campaign email cron failed", error=e)
        return {
            'ok': False,
            'error': str(e),
            **stats
        }
    
    # --- Original code below (kept for reference, not executed) ---
    supabase = get_supabase()
    today = _utc_today()
    counts: Dict[str, Any] = {
        'candidates': 0,
        'skipped_already_sent': 0,
        'skipped_no_clerk': 0,
        'skipped_no_prefs': 0,
        'allocation_errors': 0,
        'emails_sent': 0,
        'marked_no_matches_no_email': 0,
        'email_send_failures': 0,
    }

    try:
        supabase.table(SEEKER_DISCOVERY_FEED_TABLE).select('seeker_id').limit(1).execute()
    except Exception as e:
        return {'ok': False, 'error': str(e), 'today_utc': today, **counts}

    founders_res = supabase.table('founders').select(
        'id, clerk_user_id, email, name, compatibility_answers'
    ).eq('is_active', True).eq('is_deleted', False).execute()
    founders = founders_res.data or []

    targets: List[Dict[str, Any]] = []
    for f in founders:
        ca = f.get('compatibility_answers')
        if not _looks_like_saved_discovery_prefs(ca):
            continue
        if not f.get('clerk_user_id'):
            counts['skipped_no_clerk'] += 1
            continue
        targets.append(f)

    counts['candidates'] = len(targets)
    counts['skipped_no_prefs'] = len(founders) - len(targets) - counts['skipped_no_clerk']

    for f in targets:
        seeker_id = f['id']
        clerk_user_id = str(f['clerk_user_id'])
        questionnaire = dict(f.get('compatibility_answers') or {})

        try:
            row_chk = (
                supabase.table(SEEKER_DISCOVERY_FEED_TABLE)
                .select('digest_email_sent')
                .eq('seeker_id', seeker_id)
                .eq('feed_date', today)
                .execute()
            )
            if row_chk.data and row_chk.data[0].get('digest_email_sent'):
                counts['skipped_already_sent'] += 1
                continue

            # limit is determined by user's plan inside search_projects_for_seeker
            search_projects_for_seeker(
                clerk_user_id,
                questionnaire,
            )

            row_after = (
                supabase.table(SEEKER_DISCOVERY_FEED_TABLE)
                .select('digest_email_sent, project_ids')
                .eq('seeker_id', seeker_id)
                .eq('feed_date', today)
                .execute()
            )
            feed = row_after.data[0] if row_after.data else None
            if not feed:
                counts['allocation_errors'] += 1
                continue
            if feed.get('digest_email_sent'):
                counts['skipped_already_sent'] += 1
                continue

            ids = feed.get('project_ids') or []
            n_projects = len(ids) if isinstance(ids, list) else 0

            if n_projects == 0:
                _mark_discovery_digest_sent(supabase, seeker_id, today)
                counts['marked_no_matches_no_email'] += 1
                continue

            sent_ok = _send_discovery_daily_digest_email(f)
            if sent_ok:
                _mark_discovery_digest_sent(supabase, seeker_id, today)
                counts['emails_sent'] += 1
            else:
                counts['email_send_failures'] += 1
        except ValueError as ve:
            log_error(f"Discovery digest skip seeker={seeker_id}", error=ve)
            counts['allocation_errors'] += 1
        except Exception as e:
            log_error(f"Discovery digest seeker={seeker_id}", error=e)
            counts['allocation_errors'] += 1

    return {'ok': True, 'today_utc': today, **counts}


def apply_to_project(
    clerk_user_id: str,
    project_id: str,
    application_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Submit an application to join a project.
    
    Args:
        clerk_user_id: The applicant's clerk user ID
        project_id: The project to apply to
        application_data: Dict containing:
            - interest_reason: str (why interested in this project)
            - value_proposition: str (what they bring)
            - question_answers: Dict (answers to custom questions)
            - video_intro_url: Optional[str]
            - voice_intro_url: Optional[str]
    
    Returns:
        The created application record
    """
    from services import plan_service
    
    # Check daily connect limit based on subscription tier
    can_connect, current_count, max_allowed = plan_service.check_connect_limit(clerk_user_id)
    if not can_connect:
        plan = plan_service.get_founder_plan(clerk_user_id)
        plan_name = plan.get('id', 'FREE')
        if plan_name == 'FREE':
            raise ValueError(
                "Free plan allows 1 application per day. Upgrade to Pro for unlimited applications."
            )
        else:
            raise ValueError(
                f"Daily application limit reached ({current_count}/{max_allowed})."
            )
    
    supabase = get_supabase()
    
    # Get applicant's founder profile
    applicant = supabase.table('founders').select('id, name').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not applicant.data:
        raise ValueError("Profile not found")
    
    applicant_id = applicant.data[0]['id']
    applicant_name = applicant.data[0].get('name', 'Someone')
    
    # Verify project exists and is accepting applications (include founder's plan)
    project = supabase.table('projects').select(
        'id, title, founder_id, is_active, seeking_cofounder, '
        'founders!inner(id, name, email, plan)'
    ).eq('id', project_id).execute()
    
    if not project.data:
        raise ValueError("Project not found")
    
    project_data = project.data[0]
    
    if not project_data.get('is_active'):
        raise ValueError("This project is no longer active")
    
    if not project_data.get('seeking_cofounder'):
        raise ValueError("This project is no longer accepting applications")
    
    if project_data['founder_id'] == applicant_id:
        raise ValueError("You cannot apply to your own project")
    
    # Check if already applied
    existing = supabase.table('applications').select('id, status').eq(
        'applicant_id', applicant_id
    ).eq('project_id', project_id).execute()
    
    if existing.data:
        status = existing.data[0].get('status')
        if status == 'pending':
            raise ValueError("You have already applied to this project")
        elif status == 'accepted':
            raise ValueError("Your application was already accepted")
    
    # Create application
    application = {
        'applicant_id': applicant_id,
        'project_id': project_id,
        'project_owner_id': project_data['founder_id'],
        'status': 'pending',
        'interest_reason': (application_data.get('interest_reason') or '').strip()[:2000],
        'value_proposition': (application_data.get('value_proposition') or '').strip()[:2000],
        'question_answers': application_data.get('question_answers', {}),
        'video_intro_url': (application_data.get('video_intro_url') or '').strip()[:1000] or None,
        'voice_intro_url': (application_data.get('voice_intro_url') or '').strip()[:1000] or None,
    }
    
    result = supabase.table('applications').insert(application).execute()
    
    if not result.data:
        raise ValueError("Failed to submit application")
    
    application_id = result.data[0]['id']
    
    # Send notification to project owner
    try:
        owner_info = project_data.get('founders', {})
        _notify_application_received(
            owner_id=project_data['founder_id'],
            owner_email=owner_info.get('email'),
            owner_name=owner_info.get('name'),
            applicant_name=applicant_name,
            project_title=project_data.get('title'),
            application_id=application_id
        )
    except Exception as e:
        log_error(f"Failed to send application notification", error=e)
    
    # Record activation milestone
    try:
        from services import activation_service
        activation_service.record_milestone(
            applicant_id, 
            activation_service.Milestone.FIRST_SWIPE,  # Reuse existing milestone
            {'project_id': project_id, 'type': 'application'}
        )
    except Exception:
        pass
    
    return result.data[0]


def get_my_applications(clerk_user_id: str) -> List[Dict[str, Any]]:
    """
    Get all applications submitted by the current user.
    
    Returns list of applications with project and status info.
    """
    supabase = get_supabase()
    
    # Get founder ID
    founder = supabase.table('founders').select('id').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not founder.data:
        raise ValueError("Profile not found")
    
    founder_id = founder.data[0]['id']
    
    # Get all applications with project info
    applications = supabase.table('applications').select(
        '*, project:projects!project_id(id, title, description, stage, genre, '
        'founder:founders!founder_id(id, name, profile_picture_url))'
    ).eq('applicant_id', founder_id).order('created_at', desc=True).execute()
    
    if not applications.data:
        return []
    
    # Format results
    formatted = []
    for app in applications.data:
        project = app.get('project') or {}
        founder = project.get('founder') or {}
        
        formatted.append({
            'id': app['id'],
            'status': app['status'],
            'created_at': app['created_at'],
            'responded_at': app.get('responded_at'),
            'project': {
                'id': project.get('id'),
                'title': project.get('title'),
                'description': project.get('description'),
                'stage': project.get('stage'),
                'genre': project.get('genre'),
            },
            'project_owner': {
                'id': founder.get('id'),
                'name': founder.get('name'),
                'profile_picture_url': founder.get('profile_picture_url'),
            }
        })
    
    return formatted


def withdraw_application(clerk_user_id: str, application_id: str) -> Dict[str, str]:
    """
    Withdraw a pending application.
    """
    supabase = get_supabase()
    
    # Get founder ID
    founder = supabase.table('founders').select('id').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not founder.data:
        raise ValueError("Profile not found")
    
    founder_id = founder.data[0]['id']
    
    # Verify application belongs to user and is pending
    application = supabase.table('applications').select('id, status').eq(
        'id', application_id
    ).eq('applicant_id', founder_id).execute()
    
    if not application.data:
        raise ValueError("Application not found")
    
    if application.data[0]['status'] != 'pending':
        raise ValueError("Can only withdraw pending applications")
    
    # Update status to withdrawn
    supabase.table('applications').update({
        'status': 'withdrawn'
    }).eq('id', application_id).execute()
    
    return {"message": "Application withdrawn successfully"}


def skip_project(clerk_user_id: str, project_id: str) -> Dict[str, str]:
    """
    Skip a project in discovery (left swipe).
    The project will not appear in future discovery results.
    """
    supabase = get_supabase()
    
    # Get seeker's founder ID
    founder = supabase.table('founders').select('id').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not founder.data:
        raise ValueError("Profile not found")
    
    seeker_id = founder.data[0]['id']
    
    # Verify project exists and get its founder_id
    project = supabase.table('projects').select('id, founder_id').eq('id', project_id).execute()
    if not project.data:
        raise ValueError("Project not found")
    
    project_founder_id = project.data[0].get('founder_id')
    
    # Check if already skipped
    existing = supabase.table('swipes').select('id').eq(
        'swiper_id', seeker_id
    ).eq('project_id', project_id).eq('swipe_type', 'left').execute()
    
    if existing.data:
        return {"message": "Already skipped"}
    
    # Record the skip (left swipe)
    supabase.table('swipes').insert({
        'swiper_id': seeker_id,
        'swiped_id': project_founder_id,
        'project_id': project_id,
        'swipe_type': 'left',
    }).execute()
    
    return {"message": "Project skipped"}


# ============================================
# PRIVATE HELPER FUNCTIONS
# ============================================

def _validate_questionnaire(q: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize questionnaire answers."""
    role = q.get('role', 'generalist').lower()
    if role not in ROLE_OPTIONS:
        role = 'generalist'
    
    stage = q.get('stage', 'any').lower()
    if stage not in STAGE_OPTIONS:
        stage = 'any'
    
    industries = q.get('industries', [])
    if not isinstance(industries, list):
        industries = []
    industries = [i.lower() for i in industries if i.lower() in INDUSTRY_OPTIONS][:3]
    
    availability = q.get('availability', 'exploring').lower()
    if availability not in AVAILABILITY_OPTIONS:
        availability = 'exploring'
    
    priorities = q.get('priorities', [])
    if not isinstance(priorities, list):
        priorities = []
    priorities = [p.lower() for p in priorities if p.lower() in PRIORITY_OPTIONS][:2]
    
    dealbreakers = q.get('dealbreakers', [])
    if not isinstance(dealbreakers, list):
        dealbreakers = []
    dealbreakers = [d.lower() for d in dealbreakers if d.lower() in DEALBREAKER_OPTIONS]
    
    return {
        'role': role,
        'stage': stage,
        'industries': industries,
        'availability': availability,
        'priorities': priorities,
        'dealbreakers': dealbreakers,
    }


def _passes_dealbreakers(project: Dict, dealbreakers: List[str]) -> bool:
    """Check if project passes all dealbreaker filters."""
    founder = project.get('founder') or {}
    compat = project.get('compatibility_answers') or {}
    
    for db in dealbreakers:
        if db == 'remote_friendly':
            work_model = compat.get('work_model', '').lower()
            if work_model and 'remote' not in work_model and 'flexible' not in work_model:
                return False
        
        elif db == 'has_funding':
            # Check if project has funding/revenue indicators
            stage = project.get('stage', '').lower()
            if stage in ['idea']:
                return False
        
        elif db == 'founder_verified':
            if not (founder.get('linkedin_verified') or founder.get('github_verified')):
                return False
    
    return True


def _calculate_match_score(
    project: Dict, 
    questionnaire: Dict, 
    seeker_skills: set
) -> tuple:
    """
    Calculate match score between seeker questionnaire and project.
    
    Returns (score: int 0-100, match_reasons: List[str])
    """
    score = 0
    max_score = 0
    match_reasons = []
    
    compat = project.get('compatibility_answers') or {}
    founder = project.get('founder') or {}
    needed_skills = set(project.get('needed_skills') or [])
    
    # 1. Role Match (30 points)
    max_score += 30
    looking_for = compat.get('primary_role', '').lower()
    seeker_role = questionnaire['role']
    
    if seeker_role == 'generalist':
        score += 20
        match_reasons.append("Open to any role")
    elif looking_for:
        role_map = {
            'technical': ['technical', 'engineer', 'developer', 'tech'],
            'business': ['business', 'sales', 'ops', 'growth', 'marketing'],
            'product': ['product', 'design', 'pm', 'ux'],
        }
        seeker_keywords = role_map.get(seeker_role, [seeker_role])
        if any(kw in looking_for for kw in seeker_keywords):
            score += 30
            match_reasons.append(f"{seeker_role.title()} role match")
    
    # 2. Stage Match (25 points)
    max_score += 25
    project_stage = project.get('stage', '').lower()
    preferred_stage = questionnaire['stage']
    
    if preferred_stage == 'any':
        score += 20
    elif project_stage == preferred_stage:
        score += 25
        match_reasons.append(f"{project_stage.replace('_', ' ').title()} stage")
    elif project_stage in ['mvp', 'early_revenue'] and preferred_stage in ['mvp', 'early_revenue']:
        score += 15  # Close match
    
    # 3. Industry Match (20 points)
    max_score += 20
    project_genre = (project.get('genre') or '').lower()
    preferred_industries = questionnaire['industries']
    
    if not preferred_industries:
        score += 15  # No preference = partial match
    elif project_genre:
        genre_industry_map = {
            'fintech': ['fintech', 'finance', 'banking', 'payments'],
            'healthcare': ['healthcare', 'health', 'medical', 'biotech'],
            'ai_ml': ['ai', 'ml', 'machine learning', 'artificial intelligence'],
            'b2b_saas': ['b2b', 'saas', 'enterprise', 'software'],
            'consumer': ['consumer', 'b2c', 'social', 'marketplace'],
            'climate': ['climate', 'sustainability', 'cleantech', 'green'],
            'education': ['education', 'edtech', 'learning'],
            'ecommerce': ['ecommerce', 'retail', 'commerce'],
            'gaming': ['gaming', 'games', 'entertainment'],
        }
        
        for industry in preferred_industries:
            keywords = genre_industry_map.get(industry, [industry])
            if any(kw in project_genre for kw in keywords):
                score += 20
                match_reasons.append(f"{industry.replace('_', '/').upper()} industry")
                break
    
    # 4. Skills Match (15 points)
    max_score += 15
    if seeker_skills and needed_skills:
        skill_overlap = seeker_skills.intersection(needed_skills)
        if skill_overlap:
            skill_score = min(15, len(skill_overlap) * 5)
            score += skill_score
            if len(skill_overlap) >= 2:
                match_reasons.append(f"{len(skill_overlap)} skills match")
    elif not needed_skills:
        score += 10  # No specific requirements = partial match
    
    # 5. Verification Bonus (10 points)
    max_score += 10
    if founder.get('linkedin_verified') and founder.get('github_verified'):
        score += 10
        match_reasons.append("Highly verified founder")
    elif founder.get('linkedin_verified') or founder.get('github_verified'):
        score += 5
        match_reasons.append("Verified founder")
    
    # Calculate percentage
    final_score = int((score / max_score) * 100) if max_score > 0 else 0
    
    return final_score, match_reasons


def _compute_verification_info(founder: Dict) -> Dict:
    """Compute verification summary for founder."""
    linkedin_verified = founder.get('linkedin_verified', False)
    github_verified = founder.get('github_verified', False)
    
    if linkedin_verified and github_verified:
        tier = 'HIGHLY_VERIFIED'
        label = 'Highly Verified'
    elif linkedin_verified or github_verified:
        tier = 'VERIFIED'
        label = 'Verified'
    else:
        tier = 'UNVERIFIED'
        label = 'Not Verified'
    
    return {
        'tier': tier,
        'label': label,
        'linkedin_verified': linkedin_verified,
        'github_verified': github_verified,
    }


def _save_search_history(seeker_id: str, questionnaire: Dict, result_count: int) -> None:
    """Save search to history for analytics."""
    try:
        supabase = get_supabase()
        supabase.table('seeker_searches').insert({
            'seeker_id': seeker_id,
            'questionnaire': questionnaire,
            'result_count': result_count,
        }).execute()
    except Exception as e:
        log_error("Failed to save search history", error=e)


def _notify_application_received(
    owner_id: str,
    owner_email: str,
    owner_name: str,
    applicant_name: str,
    project_title: str,
    application_id: str
) -> None:
    """Send notification to project owner about new application."""
    supabase = get_supabase()
    
    # Create in-app notification (wrapped in try/catch so email still sends if this fails)
    try:
        supabase.table('notifications').insert({
            'user_id': owner_id,
            'type': 'APPLICATION_RECEIVED',
            'title': f"New application for {project_title}",
            'message': f"{applicant_name} wants to join your project",
            'data': {
                'application_id': application_id,
                'project_title': project_title,
            }
        }).execute()
    except Exception as e:
        print(f"[NOTIFY] In-app notification insert failed: {e}")
    
    # Send email notification
    print(f"[NOTIFY] _notify_application_received: owner_email={owner_email}")
    if owner_email:
        try:
            from services import email_service
            email_service.send_interest_received_email(
                to_email=owner_email,
                user_name=owner_name or 'there',
                interested_user_name=applicant_name,
                project_name=project_title
            )
        except Exception as e:
            print(f"[NOTIFY] EXCEPTION in send_interest_received_email: {e}")
            log_error("Failed to send application email", error=e)
    else:
        print("[NOTIFY] SKIP: No owner_email provided")
