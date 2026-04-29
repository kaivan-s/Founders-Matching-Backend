"""
Discovery filters for founders / projects.

Centralizes:
  1. Parsing filter params from a Flask request (query string)
  2. Post-fetch Python-side filtering (for fields that don't map well to SQL)
  3. Filter options endpoint (returns available values for picker UIs)

The DB-level filters (search, stage, genre) are applied inside
founder_service.get_available_founders against the Supabase query builder.
The richer filters here are applied in Python after the projects+founders
join is hydrated.
"""
import json
from typing import Any, Dict, List, Optional

from config.database import get_supabase
from utils.validation import sanitize_string, sanitize_list, validate_integer, validate_enum
from utils.logger import log_warning


# ----------------------------
# Constants / enums
# ----------------------------
ALLOWED_STAGES = ['idea', 'mvp', 'early_revenue', 'scaling']

VERIFICATION_TIER_ORDER = {
    'UNVERIFIED': 0,
    'VERIFIED': 1,
    'PRO_VERIFIED': 2,
    'HIGHLY_VERIFIED': 3,
}

ALLOWED_TIME_COMMITMENTS = [
    'full_time', 'part_time', 'nights_weekends', 'flexible'
]

ALLOWED_LOCATION_PREFERENCES = [
    'same_city', 'same_country', 'same_timezone', 'remote_anywhere'
]


# ----------------------------
# Parsing
# ----------------------------
def parse_discovery_filters(request_args) -> Dict[str, Any]:
    """
    Parse + validate filter params from a Flask request.args (ImmutableMultiDict).
    
    Returns a dict ready for get_available_founders / apply_python_filters.
    Unknown / invalid params are silently dropped (graceful fallback).
    """
    # Multi-value params (lists)
    stages = sanitize_list(request_args.getlist('stages'), max_items=10)
    stages = [s for s in stages if s in ALLOWED_STAGES]

    genres = sanitize_list(request_args.getlist('genres'), max_items=20)
    skills = sanitize_list(request_args.getlist('skills'), max_items=20)
    interests = sanitize_list(request_args.getlist('interests'), max_items=20)

    # Compatibility answers filter: JSON object {key: value} on URL as ?compatibility_filters={"...":"..."}
    compatibility_filters = _parse_json_param(request_args.get('compatibility_filters'))

    # Verification min tier
    min_tier_raw = sanitize_string(request_args.get('verification_min_tier', ''), max_length=30)
    min_tier = min_tier_raw if (min_tier_raw and min_tier_raw in VERIFICATION_TIER_ORDER) else None

    # Time commitment (nullable enum)
    time_commitment = validate_enum(
        request_args.get('time_commitment', ''), ALLOWED_TIME_COMMITMENTS
    )

    # Location preference (nullable enum)
    location_preference = validate_enum(
        request_args.get('location_preference', ''), ALLOWED_LOCATION_PREFERENCES
    )

    timezone_pref = sanitize_string(request_args.get('timezone', ''), max_length=80)

    # Single-value backward-compat params
    legacy_project_stage = validate_enum(
        request_args.get('project_stage', ''), ALLOWED_STAGES + ['']
    )
    legacy_genre = sanitize_string(request_args.get('genre', ''), max_length=50)
    legacy_looking_for = sanitize_string(request_args.get('looking_for', ''), max_length=100)

    return {
        # legacy single-value filters (preserved for backward compat in callers)
        'project_stage': legacy_project_stage,
        'genre': legacy_genre,
        'looking_for': legacy_looking_for,
        'search': sanitize_string(request_args.get('search', ''), max_length=200),
        'location': sanitize_string(request_args.get('location', ''), max_length=200),
        'skills': skills,

        # new multi-value filters
        'stages': stages,
        'genres': genres,
        'interests': interests,
        'compatibility_filters': compatibility_filters,
        'verification_min_tier': min_tier,
        'time_commitment': time_commitment,
        'location_preference': location_preference,
        'timezone': timezone_pref,

        # pagination
        'limit': validate_integer(request_args.get('limit', 20), min_value=1, max_value=100) or 20,
        'offset': validate_integer(request_args.get('offset', 0), min_value=0) or 0,

        # discovery preferences (used by paid-user compatibility scoring)
        'preferences': sanitize_string(request_args.get('preferences', ''), max_length=5000),
    }


def _parse_json_param(value: Optional[str]) -> Dict[str, Any]:
    """Parse a JSON object query param, or return {} on any failure."""
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items() if v not in (None, '')}
    except Exception:
        pass
    return {}


# ----------------------------
# Python-side filter application
# ----------------------------
def apply_python_filters(
    project: Dict[str, Any],
    founder_info: Dict[str, Any],
    filters: Dict[str, Any],
) -> bool:
    """
    Return True if the (project, founder) pair passes the Python-side filters.
    Used by founder_service.get_available_founders during its post-fetch filter loop.
    
    DB-level filters are applied separately in the Supabase query.
    """
    if not filters:
        return True

    # Multi-stage filter (in addition to legacy single-value project_stage)
    stages = filters.get('stages') or []
    if stages and project.get('stage') not in stages:
        return False

    # Multi-genre
    genres = filters.get('genres') or []
    if genres and project.get('genre') not in genres:
        return False

    # Founder skill intersection
    if filters.get('skills'):
        founder_skills = founder_info.get('skills') or []
        if not any(s in founder_skills for s in filters['skills']):
            return False

    # Location substring match
    if filters.get('location'):
        loc = (founder_info.get('location') or '').lower()
        if filters['location'].lower() not in loc:
            return False

    # Founder interests intersection
    interests = filters.get('interests') or []
    if interests:
        founder_interests = founder_info.get('interests') or []
        if not any(i in founder_interests for i in interests):
            return False

    # Time commitment from work_preferences
    time_commitment = filters.get('time_commitment')
    if time_commitment:
        prefs = founder_info.get('work_preferences') or {}
        if prefs.get('commitment') != time_commitment:
            return False

    # Location preference from work_preferences (e.g. only-remote founders)
    location_preference = filters.get('location_preference')
    if location_preference:
        prefs = founder_info.get('work_preferences') or {}
        if prefs.get('location_preference') != location_preference:
            return False

    # Verification minimum tier
    min_tier = filters.get('verification_min_tier')
    if min_tier:
        founder_tier = _compute_tier_from_founder(founder_info)
        if VERIFICATION_TIER_ORDER.get(founder_tier, 0) < VERIFICATION_TIER_ORDER.get(min_tier, 0):
            return False

    # Compatibility answers filter (the user's killer filter — match
    # specific answers the project owner gave during creation)
    compat_filters = filters.get('compatibility_filters') or {}
    if compat_filters:
        project_answers = project.get('compatibility_answers') or {}
        for key, expected in compat_filters.items():
            if key not in project_answers:
                return False
            actual = project_answers[key]
            # Support either single-value or list-value matching
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False

    # Search (title/description/founder name)
    if filters.get('search'):
        q = filters['search'].lower()
        haystack = ' '.join([
            (project.get('title') or '').lower(),
            (project.get('description') or '').lower(),
            (founder_info.get('name') or '').lower(),
            (founder_info.get('headline') or '').lower(),
        ])
        if q not in haystack:
            return False

    # Legacy single-field looking_for filter
    if filters.get('looking_for'):
        looking_for = (founder_info.get('looking_for') or '').lower()
        if filters['looking_for'].lower() not in looking_for:
            return False

    return True


def _compute_tier_from_founder(founder_info: Dict[str, Any]) -> str:
    """Lightweight tier computation (avoids importing verification_service in hot path)."""
    li = bool(founder_info.get('linkedin_verified'))
    gh = bool(founder_info.get('github_verified'))
    if li and gh:
        return 'HIGHLY_VERIFIED'
    if li:
        # PRO_VERIFIED requires email match — checked centrally in verification_service.
        # In the discovery hot path we use VERIFIED as a safe lower bound.
        return 'VERIFIED'
    if gh:
        return 'VERIFIED'
    return 'UNVERIFIED'


# ----------------------------
# Filter options endpoint
# ----------------------------
def get_filter_options() -> Dict[str, Any]:
    """
    Return available values for each filter, computed from live data.
    
    Used by the frontend to render filter pickers without hardcoded enums.
    Cached implicitly by the request layer; cheap enough at small scale.
    """
    supabase = get_supabase()
    options: Dict[str, Any] = {
        'stages': ALLOWED_STAGES,
        'verification_tiers': list(VERIFICATION_TIER_ORDER.keys()),
        'time_commitments': ALLOWED_TIME_COMMITMENTS,
        'location_preferences': ALLOWED_LOCATION_PREFERENCES,
    }

    # Distinct genres in active projects (top 50)
    try:
        genres = supabase.rpc('get_distinct_project_genres').execute()
        if genres.data:
            options['genres'] = [g for g in genres.data if g][:50]
    except Exception:
        # Fallback: pull a recent slice and dedupe in Python
        try:
            recent = supabase.table('projects').select('genre').eq('is_active', True).limit(500).execute()
            seen: List[str] = []
            seen_set = set()
            for row in (recent.data or []):
                g = row.get('genre')
                if g and g not in seen_set:
                    seen.append(g)
                    seen_set.add(g)
            options['genres'] = seen[:50]
        except Exception as e:
            log_warning(f"Could not compute genre options: {e}")
            options['genres'] = []

    # Top skills in use (frequency-sorted)
    try:
        skill_rows = supabase.table('founders').select('skills').limit(2000).execute()
        skill_freq: Dict[str, int] = {}
        for row in (skill_rows.data or []):
            for s in (row.get('skills') or []):
                if s:
                    skill_freq[s] = skill_freq.get(s, 0) + 1
        top_skills = sorted(skill_freq.items(), key=lambda kv: kv[1], reverse=True)[:80]
        options['skills'] = [s for s, _ in top_skills]
    except Exception as e:
        log_warning(f"Could not compute skill options: {e}")
        options['skills'] = []

    # Top interests
    try:
        interest_rows = supabase.table('founders').select('interests').limit(2000).execute()
        interest_freq: Dict[str, int] = {}
        for row in (interest_rows.data or []):
            for i in (row.get('interests') or []):
                if i:
                    interest_freq[i] = interest_freq.get(i, 0) + 1
        top_interests = sorted(interest_freq.items(), key=lambda kv: kv[1], reverse=True)[:50]
        options['interests'] = [i for i, _ in top_interests]
    except Exception as e:
        log_warning(f"Could not compute interest options: {e}")
        options['interests'] = []

    # Compatibility answer keys: pull from a sample of recent projects so the
    # frontend knows which keys to show as filter dimensions
    try:
        compat_rows = supabase.table('projects').select(
            'compatibility_answers'
        ).eq('is_active', True).not_.is_('compatibility_answers', 'null').limit(200).execute()
        key_value_freq: Dict[str, Dict[str, int]] = {}
        for row in (compat_rows.data or []):
            answers = row.get('compatibility_answers') or {}
            if isinstance(answers, dict):
                for k, v in answers.items():
                    if k and v is not None:
                        key_value_freq.setdefault(k, {})
                        v_str = str(v)
                        key_value_freq[k][v_str] = key_value_freq[k].get(v_str, 0) + 1
        # Build {key: [top_values]} structure
        compat_dims = {}
        for k, vmap in key_value_freq.items():
            top = sorted(vmap.items(), key=lambda kv: kv[1], reverse=True)[:10]
            compat_dims[k] = [val for val, _ in top]
        options['compatibility_dimensions'] = compat_dims
    except Exception as e:
        log_warning(f"Could not compute compatibility options: {e}")
        options['compatibility_dimensions'] = {}

    return options
