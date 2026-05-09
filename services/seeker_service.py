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


def _rank_discovery_candidates(
    supabase,
    seeker_id: str,
    seeker_skills: set,
    validated: Dict[str, Any],
    extra_exclude_ids: Optional[set] = None,
    visible_tiers: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return scored project rows [{project, score, match_reasons}, ...] descending by score.
    
    Args:
        visible_tiers: List of plan tiers the seeker can see (e.g., ['FREE', 'PRO']).
                      If None, shows all tiers (no filtering).
    """
    extra_exclude_ids = extra_exclude_ids or set()
    
    # Include founder's plan for tier filtering
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
    
    matched_projects = supabase.table('matches').select('project_id').execute()
    matched_project_ids = {m['project_id'] for m in (matched_projects.data or []) if m.get('project_id')}
    
    scored_projects: List[Dict[str, Any]] = []
    
    for project in result.data:
        project_id = project['id']
        if project_id in applied_project_ids or project_id in matched_project_ids:
            continue
        if project_id in extra_exclude_ids:
            continue
        
        # Tier filtering: only show projects from founders whose tier the seeker can see
        if visible_tiers:
            founder = project.get('founder') or {}
            founder_plan = founder.get('plan', 'FREE')
            if founder_plan not in visible_tiers:
                continue
        
        if not _passes_dealbreakers(project, validated['dealbreakers']):
            continue
        score, match_reasons = _calculate_match_score(project, validated, seeker_skills)
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


def _format_discovery_match(score: int, match_reasons: List[str], project: Dict[str, Any]) -> Dict[str, Any]:
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
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Hydrate formatted matches preserving order where possible.
    Top up from ranked_order if stored ids are no longer available.
    """
    formatted: List[Dict[str, Any]] = []
    used: set = set()
    
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
        formatted.append(_format_discovery_match(score, reasons, project_dict))
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


def search_projects_for_seeker(
    clerk_user_id: str,
    questionnaire: Dict[str, Any],
    limit: int = 3,
) -> Dict[str, Any]:
    """
    Find matching projects for seeker. Uses a daily feed of up to
    DAILY_DISCOVERY_BATCH_SIZE curated projects per UTC day when
    `seeker_discovery_daily_feed` exists; otherwise falls back to a single ranking.
    
    Tier-based filtering (Option A - downward visibility):
    - FREE users can only see FREE founders' projects
    - PRO users can see FREE + PRO founders' projects  
    - PRO_PLUS users can see all tiers
    
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
    
    # Get visible tiers based on seeker's subscription plan
    visible_tiers = plan_service.get_visible_tiers(clerk_user_id)
    
    validated = _validate_questionnaire(questionnaire)
    pref_key = _preference_key(validated)
    batch_cap = min(DAILY_DISCOVERY_BATCH_SIZE, max(1, min(int(limit or DAILY_DISCOVERY_BATCH_SIZE), DAILY_DISCOVERY_BATCH_SIZE)))

    existing_applications = supabase.table('applications').select('project_id').eq(
        'applicant_id', seeker_id
    ).execute()
    applied_project_ids = {app['project_id'] for app in (existing_applications.data or [])}
    
    matched_projects = supabase.table('matches').select('project_id').execute()
    matched_project_ids = {m['project_id'] for m in (matched_projects.data or []) if m.get('project_id')}
    
    discovery_meta: Dict[str, Any] = {
        'daily_limit': DAILY_DISCOVERY_BATCH_SIZE,
        'effective_limit': batch_cap,
        'next_batch_at_utc': _next_utc_midnight().isoformat(),
        'persistent_feed': False,
        'today_utc': _utc_today(),
        'visible_tiers': visible_tiers,  # Include for frontend to show upgrade prompts
    }

    def _ids_from_ranked(ranked_rows: List[Dict[str, Any]], cap: int) -> List[str]:
        return [r['project']['id'] for r in ranked_rows[:cap]]

    def _baseline_ranked(extra_exclude: Optional[set] = None) -> List[Dict[str, Any]]:
        return _rank_discovery_candidates(
            supabase, seeker_id, seeker_skills, validated,
            extra_exclude_ids=extra_exclude,
            visible_tiers=visible_tiers,
        )

    today = discovery_meta['today_utc']

    # If the feed table is missing, degrade gracefully (no daily cap across sessions).
    try:
        supabase.table(SEEKER_DISCOVERY_FEED_TABLE).select('seeker_id').limit(1).execute()
    except Exception as e:
        log_error(f"Discovery feed unavailable — falling back ({SEEKER_DISCOVERY_FEED_TABLE})", error=e)
        ranked = _baseline_ranked()
        rmap = {r['project']['id']: r for r in ranked}
        formatted, _ = _build_matches_from_ordered_ids(
            _ids_from_ranked(ranked, batch_cap),
            rmap,
            ranked,
            validated=validated,
            seeker_skills=seeker_skills,
            seeker_id=seeker_id,
            applied_project_ids=applied_project_ids,
            matched_project_ids=matched_project_ids,
            batch_cap=batch_cap,
        )
        discovery_meta['note'] = (
            'Daily discovery feed table missing — run supabase/snippets/seeker_discovery_daily_feed.sql'
        )
        _save_search_history(seeker_id, validated, len(formatted))
        return {'matches': formatted, 'discovery': discovery_meta}

    discovery_meta['persistent_feed'] = True

    historical = _historical_discovery_ids(supabase, seeker_id, today)
    ranked_for_hydrate = _baseline_ranked(extra_exclude=historical)
    ranked_by_id_hist = {r['project']['id']: r for r in ranked_for_hydrate}

    row_res = (
        supabase.table(SEEKER_DISCOVERY_FEED_TABLE)
        .select('*')
        .eq('seeker_id', seeker_id)
        .eq('feed_date', today)
        .execute()
    )
    existing_row = row_res.data[0] if row_res.data else None

    needing_new_allocation = (
        existing_row is None or existing_row.get('preference_key') != pref_key
    )

    if needing_new_allocation:
        starter_ids = _ids_from_ranked(ranked_for_hydrate, batch_cap)
        persist = {'seeker_id': seeker_id, 'feed_date': today}
        formatted, final_ids = _build_matches_from_ordered_ids(
            starter_ids,
            ranked_by_id_hist,
            ranked_for_hydrate,
            validated=validated,
            seeker_skills=seeker_skills,
            seeker_id=seeker_id,
            applied_project_ids=applied_project_ids,
            matched_project_ids=matched_project_ids,
            batch_cap=batch_cap,
        )
        try:
            supabase.table(SEEKER_DISCOVERY_FEED_TABLE).upsert({
                **persist,
                'project_ids': final_ids,
                'preference_key': pref_key,
                'digest_email_sent': False,
            }, on_conflict='seeker_id,feed_date').execute()
        except Exception as e:
            log_error('Failed to upsert seeker_discovery_daily_feed', error=e)
        _save_search_history(seeker_id, validated, len(formatted))
        return {'matches': formatted, 'discovery': discovery_meta}

    stored_ids = list(existing_row.get('project_ids') or [])[:batch_cap]

    formatted, final_ids = _build_matches_from_ordered_ids(
        stored_ids,
        ranked_by_id_hist,
        ranked_for_hydrate,
        validated=validated,
        seeker_skills=seeker_skills,
        seeker_id=seeker_id,
        applied_project_ids=applied_project_ids,
        matched_project_ids=matched_project_ids,
        batch_cap=batch_cap,
    )
    try:
        if final_ids != stored_ids:
            preserve_digest = bool(existing_row.get('digest_email_sent')) if existing_row else False
            supabase.table(SEEKER_DISCOVERY_FEED_TABLE).upsert({
                'seeker_id': seeker_id,
                'feed_date': today,
                'project_ids': final_ids,
                'preference_key': pref_key,
                'digest_email_sent': preserve_digest,
            }, on_conflict='seeker_id,feed_date').execute()
    except Exception as e:
        log_error('Failed to refresh discovery feed ids', error=e)

    _save_search_history(seeker_id, validated, len(formatted))
    return {'matches': formatted, 'discovery': discovery_meta}


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
    For each founder with saved discovery questionnaire answers:
    ensure today's batch exists (same logic as Discover search), then send one
    digest email per UTC day (digest_email_sent) driving users back into the app.

    Call from POST /api/cron/discovery-daily-digest (scheduler), not from user HTTP.
    """
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
    ).execute()
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

            search_projects_for_seeker(
                clerk_user_id,
                questionnaire,
                limit=DAILY_DISCOVERY_BATCH_SIZE,
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
    
    # Verify project exists and is accepting applications
    project = supabase.table('projects').select(
        'id, title, founder_id, is_active, seeking_cofounder, '
        'founders!inner(id, name, email)'
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
    
    # Create in-app notification
    supabase.table('notifications').insert({
        'user_id': owner_id,
        'type': 'application_received',
        'title': f"New application for {project_title}",
        'message': f"{applicant_name} wants to join your project",
        'data': {
            'application_id': application_id,
            'project_title': project_title,
        }
    }).execute()
    
    # Send email notification
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
            log_error("Failed to send application email", error=e)
