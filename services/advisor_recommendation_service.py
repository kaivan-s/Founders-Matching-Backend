"""Service for generating advisor recommendations for workspaces"""
from config.database import get_supabase
from datetime import datetime, timezone
from typing import List, Dict, Optional
from utils.logger import log_info, log_error

def calculate_advisor_recommendation_score(advisor_id: str, workspace_id: str) -> Dict:
    """
    Calculate recommendation score for an advisor joining a workspace.
    Returns dict with score and reasons.
    """
    supabase = get_supabase()
    
    # Get workspace info
    workspace = supabase.table('workspaces').select('*, projects(*), workspace_participants(*, founders(*))').eq('id', workspace_id).execute()
    if not workspace.data:
        return {'score': 0.0, 'reasons': []}
    
    workspace_info = workspace.data[0]
    project = workspace_info.get('projects', {}) if isinstance(workspace_info.get('projects'), dict) else {}
    participants = workspace_info.get('workspace_participants', []) or []
    
    # Get advisor profile
    advisor_profile_result = supabase.table('advisor_profiles').select('*').eq('user_id', advisor_id).execute()
    if not advisor_profile_result.data:
        return {'score': 0.0, 'reasons': []}
    
    advisor_profile = advisor_profile_result.data[0]
    
    score = 0.0
    reasons = []
    
    # 1. Domain expertise match (0-30 points)
    project_genre = project.get('genre', '').lower()
    advisor_domains = advisor_profile.get('domain_expertise', []) or []
    
    if project_genre and advisor_domains:
        domain_match = any(domain.lower() == project_genre for domain in advisor_domains)
        if domain_match:
            score += 30
            reasons.append(f"Expertise in {project_genre} domain")
        else:
            score += 10
            reasons.append("Some relevant domain experience")
    else:
        score += 15  # Neutral
    
    # 2. Stage expertise match (0-25 points)
    project_stage = project.get('stage', '').lower()
    advisor_stages = advisor_profile.get('stage_expertise', []) or []
    
    if project_stage and advisor_stages:
        stage_match = any(stage.lower() == project_stage for stage in advisor_stages)
        if stage_match:
            score += 25
            reasons.append(f"Experience with {project_stage} stage startups")
        else:
            score += 10
    else:
        score += 12.5
    
    # 3. Years of experience (0-20 points)
    years_experience = advisor_profile.get('years_experience', '')
    experience_scores = {
        'less_than_2': 5,
        '2_5': 10,
        '5_10': 15,
        '10_plus': 20
    }
    score += experience_scores.get(years_experience, 10)
    if years_experience:
        reasons.append(f"{years_experience.replace('_', '-')} years of experience")
    
    # 4. Advisor score/rating (0-15 points)
    advisor_score = advisor_profile.get('advisor_score', 0) or 0
    if advisor_score > 0:
        score += min(advisor_score / 10, 15)  # Max 15 points for score
        reasons.append(f"High advisor rating ({advisor_score})")
    
    # 5. Availability (0-10 points)
    current_active = advisor_profile.get('current_active_workspaces', 0) or 0
    max_active = advisor_profile.get('max_active_workspaces', 0) or 0
    
    if max_active > 0:
        availability_ratio = 1 - (current_active / max_active)
        score += availability_ratio * 10
        if availability_ratio > 0.5:
            reasons.append("Good availability")
    
    return {
        'score': round(score, 2),
        'reasons': reasons,
        'advisor_id': advisor_id,
        'workspace_id': workspace_id
    }

def generate_advisor_recommendations(workspace_id: str, limit: int = 10) -> List[Dict]:
    """
    Generate advisor recommendations for a workspace.
    Returns list of advisors sorted by recommendation score.
    """
    supabase = get_supabase()
    
    # Get all approved, discoverable advisors
    advisors = supabase.table('advisor_profiles').select(
        '*, founders!inner(id, name, location, skills)'
    ).eq('status', 'APPROVED').eq('is_discoverable', True).execute()
    
    if not advisors.data:
        return []
    
    recommendations = []
    
    for advisor_profile in advisors.data:
        advisor_id = advisor_profile.get('user_id')
        if not advisor_id:
            continue
        
        try:
            rec_data = calculate_advisor_recommendation_score(advisor_id, workspace_id)
            
            # Add advisor info
            rec_data['advisor'] = {
                'id': advisor_id,
                'name': advisor_profile.get('founders', {}).get('name') if isinstance(advisor_profile.get('founders'), dict) else None,
                'location': advisor_profile.get('founders', {}).get('location') if isinstance(advisor_profile.get('founders'), dict) else None,
                'skills': advisor_profile.get('founders', {}).get('skills') if isinstance(advisor_profile.get('founders'), dict) else [],
                'domain_expertise': advisor_profile.get('domain_expertise', []),
                'years_experience': advisor_profile.get('years_experience', ''),
                'advisor_score': advisor_profile.get('advisor_score', 0)
            }
            
            recommendations.append(rec_data)
        except Exception as e:
            log_error(f"Failed to calculate recommendation for advisor {advisor_id}", error=e)
            continue
    
    # Sort by score descending
    recommendations.sort(key=lambda x: x['score'], reverse=True)
    
    # Save top recommendations to database
    for i, rec in enumerate(recommendations[:limit]):
        try:
            supabase.table('advisor_recommendations').upsert({
                'advisor_id': rec['advisor_id'],
                'workspace_id': workspace_id,
                'recommendation_score': rec['score'],
                'reasons': rec['reasons'],
                'is_manual': False
            }, on_conflict='advisor_id,workspace_id').execute()
        except Exception as e:
            log_error(f"Failed to save recommendation for advisor {rec['advisor_id']}", error=e)
    
    return recommendations[:limit]

def get_advisor_recommendations(workspace_id: str, include_manual: bool = True) -> List[Dict]:
    """Get stored advisor recommendations for a workspace"""
    supabase = get_supabase()
    
    query = supabase.table('advisor_recommendations').select(
        '*, advisors:founders!advisor_id(*), workspace:workspaces!workspace_id(*)'
    ).eq('workspace_id', workspace_id).order('recommendation_score', desc=True)
    
    if not include_manual:
        query = query.eq('is_manual', False)
    
    result = query.execute()
    return result.data if result.data else []
