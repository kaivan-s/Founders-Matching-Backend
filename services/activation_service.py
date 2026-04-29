"""
Activation service — funnel gating, bio templates, milestones, first-match coaching.

Activation strategy (predicts long-term retention):
  - Force profile to >= 60% complete before user appears in discovery.
    Half-empty profiles drag down match quality for everyone.
  - Provide curated bio templates so users have a starting point instead of
    "Passionate builder excited about startups" copypasta.
  - Track every funnel milestone in activation_milestones so we can compute
    time-to-first-match, drop-off points, and drive nudges.
  - At first match, surface a coaching panel: schedule the first call within
    7 days + suggested questions.
"""
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from config.database import get_supabase
from services import profile_service
from utils.logger import log_info, log_warning


# ============================================================
# Canonical milestone names (keep in sync with the migration's COMMENT)
# ============================================================
class Milestone:
    SIGNED_UP = 'SIGNED_UP'
    PROFILE_STARTED = 'PROFILE_STARTED'
    PROFILE_COMPLETE = 'PROFILE_COMPLETE'
    LINKEDIN_VERIFIED = 'LINKEDIN_VERIFIED'
    GITHUB_VERIFIED = 'GITHUB_VERIFIED'
    FIRST_PROJECT_CREATED = 'FIRST_PROJECT_CREATED'
    FIRST_SWIPE = 'FIRST_SWIPE'
    FIRST_MATCH = 'FIRST_MATCH'
    FIRST_MESSAGE_SENT = 'FIRST_MESSAGE_SENT'
    FIRST_CALL_SCHEDULED = 'FIRST_CALL_SCHEDULED'
    FIRST_CALL_COMPLETED = 'FIRST_CALL_COMPLETED'
    FOUNDER_DATE_STAGE_2 = 'FOUNDER_DATE_STAGE_2'
    FOUNDER_DATE_STAGE_3 = 'FOUNDER_DATE_STAGE_3'
    FOUNDER_DATE_COMPLETED = 'FOUNDER_DATE_COMPLETED'
    WORKSPACE_CREATED = 'WORKSPACE_CREATED'
    EQUITY_AGREEMENT_PURCHASED = 'EQUITY_AGREEMENT_PURCHASED'


# Order matters — drives the "next milestone" hint
MILESTONE_ORDER: List[str] = [
    Milestone.SIGNED_UP,
    Milestone.PROFILE_STARTED,
    Milestone.PROFILE_COMPLETE,
    Milestone.LINKEDIN_VERIFIED,
    Milestone.FIRST_PROJECT_CREATED,
    Milestone.FIRST_SWIPE,
    Milestone.FIRST_MATCH,
    Milestone.FIRST_MESSAGE_SENT,
    Milestone.FIRST_CALL_SCHEDULED,
    Milestone.FIRST_CALL_COMPLETED,
    Milestone.FOUNDER_DATE_STAGE_2,
    Milestone.FOUNDER_DATE_STAGE_3,
    Milestone.FOUNDER_DATE_COMPLETED,
    Milestone.WORKSPACE_CREATED,
    Milestone.EQUITY_AGREEMENT_PURCHASED,
]

# Discovery visibility threshold. Below this, founder is hidden from swipe feed
# and their projects don't surface. Tunable based on data.
DISCOVERY_VISIBILITY_THRESHOLD = 60


# ============================================================
# Bio templates — curated starting points for the bio field
# Categorized by founder archetype. Frontend shows these in a "pick a template"
# step during onboarding instead of a blank textarea.
# ============================================================
BIO_TEMPLATES: List[Dict[str, str]] = [
    {
        'id': 'technical_first_time',
        'archetype': 'Technical, first-time founder',
        'template': (
            "Engineer with {years} years building {domain}. Most recently {recent_role} "
            "at {recent_company}. Currently {current_situation} and looking for a "
            "{cofounder_type} co-founder to {primary_goal}. I work best when I {work_style}."
        ),
        'example': (
            "Engineer with 7 years building consumer apps. Most recently Senior iOS Engineer "
            "at Meta. Currently saving runway to go full-time and looking for a business-side "
            "co-founder to take an idea from prototype to revenue. I work best when I have "
            "deep focus blocks in the morning and ship daily."
        ),
    },
    {
        'id': 'business_first_time',
        'archetype': 'Business, first-time founder',
        'template': (
            "{role} with {years} years in {industry}. Built {past_achievement} at {company}. "
            "I'm exploring an idea in {space} and need a technical co-founder who {requirement}. "
            "I bring {strength_1}, {strength_2}, and a network in {network_area}."
        ),
        'example': (
            "PM with 6 years in B2B SaaS. Built and scaled the analytics product to $4M ARR at "
            "Segment. I'm exploring an idea in AI for sales ops and need a technical co-founder "
            "who has shipped LLM products before. I bring product strategy, GTM motion, and a "
            "network in mid-market sales tooling."
        ),
    },
    {
        'id': 'repeat_founder',
        'archetype': 'Repeat / experienced founder',
        'template': (
            "Founder of {past_company} ({outcome}). Spent {years} doing {role}. I'm working on "
            "{thesis} and looking for a {cofounder_type} co-founder who {requirement}. The bar "
            "is {bar_metric} — I'm post-{stage} on this idea."
        ),
        'example': (
            "Founder of Hopin clone in MENA (acquired 2023). Spent 8 years doing product + GTM. "
            "I'm working on the thesis that vertical AI for legal will compress white-collar "
            "billing 50% in 5 years and looking for a technical co-founder who has shipped "
            "regulated software before. The bar is shipping in 3 months — I'm post-research on "
            "this idea."
        ),
    },
    {
        'id': 'student',
        'archetype': 'Student / very early-stage',
        'template': (
            "{year} at {university} studying {major}. Built {past_project} {past_outcome}. "
            "Want to start something in {area} after graduation. Looking for a co-founder who "
            "is also {commitment_level} and can help with {gap}."
        ),
        'example': (
            "Junior at IIT Bombay studying Computer Science. Built a scheduling app that 3,000 "
            "students on campus actually use. Want to start something in B2B AI after "
            "graduation. Looking for a co-founder who is also full-time-after-graduation and "
            "can help with sales / GTM."
        ),
    },
    {
        'id': 'specialist',
        'archetype': 'Domain specialist',
        'template': (
            "{domain} specialist with {years} years at {types_of_companies}. I see {specific_pain} "
            "in {industry} every week and want to build the tool that fixes it. Looking for a "
            "co-founder who is {requirement}."
        ),
        'example': (
            "ICU nurse with 11 years at three hospital systems. I see clinicians spending 2 "
            "hours/shift on charting and want to build the tool that fixes it. Looking for a "
            "co-founder who is technical and has a stomach for healthcare regulation."
        ),
    },
]


# ============================================================
# Helpers
# ============================================================
def _get_founder_id(clerk_user_id: str) -> Optional[str]:
    supabase = get_supabase()
    result = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if result.data:
        return result.data[0]['id']
    return None


# ============================================================
# Profile completeness gate
# ============================================================
def is_visible_in_discovery(clerk_user_id: str) -> bool:
    """
    True only if profile completeness >= threshold AND has at least 1 active
    project. Used to gate visibility in the swipe feed for OTHERS.
    """
    try:
        completeness = profile_service.get_profile_completeness(clerk_user_id)
        if completeness.get('score', 0) < DISCOVERY_VISIBILITY_THRESHOLD:
            return False
    except Exception:
        return False

    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        return False

    supabase = get_supabase()
    projects = supabase.table('projects').select('id', count='exact').eq(
        'founder_id', founder_id
    ).eq('is_active', True).eq('seeking_cofounder', True).execute()

    return (projects.count or 0) >= 1


# ============================================================
# Milestone tracking (idempotent — first hit wins)
# ============================================================
def record_milestone(
    founder_id: str,
    milestone: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Record a milestone (idempotent). First call inserts; subsequent calls
    are no-ops thanks to the unique constraint.

    Returns the stored row, or None if insert failed silently (which can
    happen if the milestones table doesn't exist yet pre-migration).
    """
    if milestone not in MILESTONE_ORDER:
        log_warning(f"Unknown milestone '{milestone}' — recording anyway")

    supabase = get_supabase()
    try:
        result = supabase.table('activation_milestones').insert({
            'founder_id': founder_id,
            'milestone': milestone,
            'metadata': metadata or {},
        }).execute()
        if result.data:
            log_info(f"Milestone recorded: {founder_id} -> {milestone}")
            return result.data[0]
    except Exception as e:
        # Could be: (a) duplicate (already recorded — harmless), (b) table missing
        msg = str(e).lower()
        if 'duplicate' in msg or 'unique' in msg:
            return None  # Already recorded — fine
        log_warning(f"Milestone insert failed: {e}")
    return None


def record_milestone_for_user(clerk_user_id: str, milestone: str, metadata: Optional[Dict] = None):
    """Convenience: resolve clerk_user_id then record milestone."""
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        return None
    return record_milestone(founder_id, milestone, metadata)


def list_milestones(clerk_user_id: str) -> List[Dict[str, Any]]:
    """Return all milestones recorded for the user, in chronological order."""
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        return []

    supabase = get_supabase()
    try:
        result = supabase.table('activation_milestones').select('*').eq(
            'founder_id', founder_id
        ).order('completed_at').execute()
        return result.data or []
    except Exception as e:
        log_warning(f"Could not list milestones: {e}")
        return []


# ============================================================
# Activation status (the main aggregator endpoint)
# ============================================================
def get_activation_status(clerk_user_id: str) -> Dict[str, Any]:
    """
    Aggregate everything the frontend needs to render the activation panel:
      - Profile completeness score + missing fields
      - Discovery visibility status (am I being shown to others?)
      - Milestones reached + the next milestone to nudge towards
      - Time since signup (for drip-campaign decisions)
    """
    completeness = profile_service.get_profile_completeness(clerk_user_id)
    visible = is_visible_in_discovery(clerk_user_id)
    reached = list_milestones(clerk_user_id)
    reached_set = {m['milestone'] for m in reached}

    # Determine next milestone in canonical order
    next_milestone = next(
        (m for m in MILESTONE_ORDER if m not in reached_set),
        None,
    )

    return {
        'profile_completeness': completeness,
        'visible_in_discovery': visible,
        'discovery_visibility_threshold': DISCOVERY_VISIBILITY_THRESHOLD,
        'milestones_reached': reached,
        'milestones_count': len(reached_set),
        'milestones_total': len(MILESTONE_ORDER),
        'next_milestone': next_milestone,
        'next_milestone_hint': _hint_for_milestone(next_milestone),
    }


def _hint_for_milestone(milestone: Optional[str]) -> Optional[Dict[str, str]]:
    """Human-readable nudge for each milestone (rendered in the activation panel)."""
    hints = {
        Milestone.PROFILE_STARTED: {
            'title': 'Add your headline and bio',
            'cta': 'Edit profile',
            'reason': 'Founders skip empty profiles. Even a one-liner doubles your match rate.',
        },
        Milestone.PROFILE_COMPLETE: {
            'title': 'Complete your profile to unlock discovery',
            'cta': 'Finish profile',
            'reason': f'Profiles below {DISCOVERY_VISIBILITY_THRESHOLD}% complete are hidden from others.',
        },
        Milestone.LINKEDIN_VERIFIED: {
            'title': 'Verify with LinkedIn for a Verified badge',
            'cta': 'Connect LinkedIn',
            'reason': 'Verified profiles get 5x more matches. Takes 30 seconds.',
        },
        Milestone.FIRST_PROJECT_CREATED: {
            'title': 'Create your first project',
            'cta': 'Create project',
            'reason': 'Your project is what other founders swipe on.',
        },
        Milestone.FIRST_SWIPE: {
            'title': 'Browse the discovery feed',
            'cta': 'Start swiping',
            'reason': 'See who else is building right now.',
        },
        Milestone.FIRST_MATCH: {
            'title': 'You\u2019ve matched! Send the first message',
            'cta': 'Open messages',
            'reason': 'Most matches die in DM purgatory. Send the first message within 24 hours.',
        },
        Milestone.FIRST_CALL_SCHEDULED: {
            'title': 'Schedule your first Founder Date',
            'cta': 'Start a Founder Date',
            'reason': 'Text-only matches rarely become co-founders. Hop on a 30-min call.',
        },
        Milestone.FIRST_CALL_COMPLETED: {
            'title': 'Submit your evaluation',
            'cta': 'Rate the call',
            'reason': 'Both founders rating helps decide whether to advance.',
        },
        Milestone.FOUNDER_DATE_COMPLETED: {
            'title': 'Open the equity calculator together',
            'cta': 'Start equity scenario',
            'reason': 'You\u2019ve cleared all 3 stages. Time to talk numbers.',
        },
        Milestone.WORKSPACE_CREATED: {
            'title': 'Generate your co-founder agreement',
            'cta': 'Generate agreement',
            'reason': 'Lock in equity terms with a lawyer-reviewed template.',
        },
    }
    return hints.get(milestone) if milestone else None


# ============================================================
# Bio templates
# ============================================================
def get_bio_templates() -> List[Dict[str, str]]:
    """Return the curated bio templates for the picker UI."""
    return BIO_TEMPLATES


# ============================================================
# First-match coaching
# ============================================================
def get_first_match_coaching(clerk_user_id: str, match_id: str) -> Dict[str, Any]:
    """
    Return coaching content for a match: deadline, suggested first message
    questions, founder date CTA. Idempotent — same payload regardless of
    whether they've already opened it.
    """
    founder_id = _get_founder_id(clerk_user_id)
    if not founder_id:
        raise ValueError("Founder not found")

    supabase = get_supabase()
    match = supabase.table('matches').select('*').eq('id', match_id).execute()
    if not match.data:
        raise ValueError("Match not found")

    match_row = match.data[0]
    if founder_id not in (match_row.get('user_a_id'), match_row.get('user_b_id')):
        raise ValueError("Not authorized for this match")

    # Compute the 7-day deadline
    created_at = match_row.get('created_at')
    deadline = None
    if created_at:
        try:
            created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            deadline = (created + timedelta(days=7)).isoformat()
        except Exception:
            pass

    return {
        'match_id': match_id,
        'created_at': created_at,
        'first_call_deadline': deadline,
        'first_message_questions': [
            'What about my profile/project caught your eye?',
            'Are you exploring full-time or part-time right now?',
            'What does your ideal first call look like?',
        ],
        'founder_date_cta': {
            'label': 'Start a Founder Date',
            'description': (
                'A structured 3-call sequence to evaluate co-founder fit, with prompts '
                'and post-call evaluations.'
            ),
        },
        'tips': [
            'Send the first message within 24 hours — match-to-message rate drops 5x after that.',
            'Aim to schedule the first call within 7 days.',
            'Lead with one specific thing you noticed about their profile.',
        ],
    }
