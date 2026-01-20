"""Service for calculating and storing compatibility scores"""
from config.database import get_supabase
from datetime import datetime, timezone
from typing import Dict, Optional
from utils.logger import log_info, log_error

def calculate_compatibility_score(founder1_id: str, founder2_id: str, project_id: str = None) -> Dict[str, float]:
    """
    Calculate compatibility score breakdown between two founders.
    Returns dict with overall_score and component scores.
    """
    supabase = get_supabase()
    
    # Get founder profiles
    founders = supabase.table('founders').select('*').in_('id', [founder1_id, founder2_id]).execute()
    if not founders.data or len(founders.data) != 2:
        return {'overall_score': 0.0}
    
    founder1 = founders.data[0] if founders.data[0]['id'] == founder1_id else founders.data[1]
    founder2 = founders.data[1] if founders.data[0]['id'] == founder1_id else founders.data[0]
    
    # Get project if provided
    project = None
    if project_id:
        project_result = supabase.table('projects').select('*').eq('id', project_id).execute()
        if project_result.data:
            project = project_result.data[0]
    
    scores = {
        'skills_match_score': 0.0,
        'stage_alignment_score': 0.0,
        'location_preference_score': 0.0,
        'work_style_score': 0.0,
        'overall_score': 0.0
    }
    
    # 1. Skills Match Score (0-100)
    founder1_skills = set(founder1.get('skills', []) or [])
    founder2_skills = set(founder2.get('skills', []) or [])
    
    if project:
        project_needed_skills = set(project.get('needed_skills', []) or [])
        # Check if founder2 has skills that project needs
        matching_skills = project_needed_skills.intersection(founder2_skills)
        if project_needed_skills:
            scores['skills_match_score'] = (len(matching_skills) / len(project_needed_skills)) * 100
        else:
            # Fallback: check skill overlap between founders
            if founder1_skills or founder2_skills:
                all_skills = founder1_skills.union(founder2_skills)
                common_skills = founder1_skills.intersection(founder2_skills)
                scores['skills_match_score'] = (len(common_skills) / len(all_skills)) * 100 if all_skills else 50.0
            else:
                scores['skills_match_score'] = 50.0  # Neutral if no skills
    else:
        # No project context - check general skill overlap
        if founder1_skills or founder2_skills:
            all_skills = founder1_skills.union(founder2_skills)
            common_skills = founder1_skills.intersection(founder2_skills)
            scores['skills_match_score'] = (len(common_skills) / len(all_skills)) * 100 if all_skills else 50.0
        else:
            scores['skills_match_score'] = 50.0
    
    # 2. Stage Alignment Score (0-100)
    founder1_looking = founder1.get('looking_for', '').lower()
    founder2_stage = founder2.get('project_stage', '').lower() if not project else project.get('stage', '').lower()
    
    stage_keywords = {
        'idea': ['idea', 'concept', 'early'],
        'mvp': ['mvp', 'prototype', 'building'],
        'early_revenue': ['revenue', 'traction', 'customers'],
        'scaling': ['scaling', 'growth', 'scale']
    }
    
    stage_match = False
    for stage, keywords in stage_keywords.items():
        if founder2_stage == stage:
            stage_match = any(keyword in founder1_looking for keyword in keywords)
            break
    
    scores['stage_alignment_score'] = 100.0 if stage_match else 50.0
    
    # 3. Location Preference Score (0-100)
    founder1_location = founder1.get('location', '').lower()
    founder2_location = founder2.get('location', '').lower()
    
    if founder1_location and founder2_location:
        # Exact match
        if founder1_location == founder2_location:
            scores['location_preference_score'] = 100.0
        # Partial match (same city or country)
        elif founder1_location.split(',')[0].strip() == founder2_location.split(',')[0].strip():
            scores['location_preference_score'] = 75.0
        else:
            scores['location_preference_score'] = 25.0
    else:
        scores['location_preference_score'] = 50.0  # Neutral if location not specified
    
    # 4. Work Style Score (0-100) - Based on compatibility answers if available
    founder1_answers = founder1.get('compatibility_answers', {}) or {}
    founder2_answers = founder2.get('compatibility_answers', {}) or {}
    
    if project:
        project_answers = project.get('compatibility_answers', {}) or {}
        founder2_answers = project_answers  # Use project answers if available
    
    work_style_matches = 0
    total_questions = 0
    
    # Compare compatibility answers
    for key in founder1_answers:
        if key in founder2_answers:
            total_questions += 1
            if founder1_answers[key] == founder2_answers[key]:
                work_style_matches += 1
    
    if total_questions > 0:
        scores['work_style_score'] = (work_style_matches / total_questions) * 100
    else:
        scores['work_style_score'] = 50.0  # Neutral if no compatibility data
    
    # Calculate overall score (weighted average)
    weights = {
        'skills_match_score': 0.35,
        'stage_alignment_score': 0.25,
        'location_preference_score': 0.15,
        'work_style_score': 0.25
    }
    
    scores['overall_score'] = (
        scores['skills_match_score'] * weights['skills_match_score'] +
        scores['stage_alignment_score'] * weights['stage_alignment_score'] +
        scores['location_preference_score'] * weights['location_preference_score'] +
        scores['work_style_score'] * weights['work_style_score']
    )
    
    return scores

def save_compatibility_score(match_id: str, founder1_id: str, founder2_id: str, project_id: str = None) -> Dict:
    """Calculate and save compatibility score for a match"""
    supabase = get_supabase()
    
    # Calculate scores
    scores = calculate_compatibility_score(founder1_id, founder2_id, project_id)
    
    # Save to database
    try:
        score_data = {
            'match_id': match_id,
            'founder1_id': founder1_id,
            'founder2_id': founder2_id,
            'project_id': project_id,
            'overall_score': round(scores['overall_score'], 2),
            'skills_match_score': round(scores['skills_match_score'], 2),
            'stage_alignment_score': round(scores['stage_alignment_score'], 2),
            'location_preference_score': round(scores['location_preference_score'], 2),
            'work_style_score': round(scores['work_style_score'], 2)
        }
        
        result = supabase.table('compatibility_scores').insert(score_data).execute()
        log_info(f"Saved compatibility score for match {match_id}")
        return result.data[0] if result.data else score_data
    except Exception as e:
        log_error(f"Failed to save compatibility score for match {match_id}", error=e)
        return scores  # Return calculated scores even if save fails

def get_compatibility_score(match_id: str) -> Optional[Dict]:
    """Get compatibility score for a match"""
    supabase = get_supabase()
    
    result = supabase.table('compatibility_scores').select('*').eq('match_id', match_id).execute()
    if result.data:
        return result.data[0]
    return None
