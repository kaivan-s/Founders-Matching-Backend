"""
Market Intelligence Service - Skill demand/supply analysis for Pro users.

This service provides insights about the co-founder marketplace:
- Which skills are in highest demand
- Which skills are oversupplied
- How a user's skills compare to market demand
- Personalized positioning advice
"""
from typing import Dict, List, Any, Tuple
from collections import Counter
from config.database import get_supabase
from utils.logger import log_info, log_error


def get_skill_market_data() -> Dict[str, Any]:
    """
    Calculate market-wide skill demand and supply.
    
    Returns aggregated data about all skills on the platform.
    This is cached and refreshed periodically (not per-request).
    """
    supabase = get_supabase()
    
    # Get all active projects seeking co-founders
    projects = supabase.table('projects').select(
        'id, needed_skills'
    ).eq('is_active', True).eq('seeking_cofounder', True).eq('is_deleted', False).execute()
    
    # Get all founders with skills
    founders = supabase.table('founders').select(
        'id, skills'
    ).execute()
    
    # Count skill demand (from projects)
    demand_counter = Counter()
    for project in (projects.data or []):
        needed = project.get('needed_skills') or []
        for skill in needed:
            # Handle both string skills and object skills
            skill_name = skill if isinstance(skill, str) else skill.get('skill', skill)
            if skill_name:
                demand_counter[skill_name.strip()] += 1
    
    # Count skill supply (from founders)
    supply_counter = Counter()
    for founder in (founders.data or []):
        skills = founder.get('skills') or []
        for skill in skills:
            skill_name = skill if isinstance(skill, str) else skill.get('skill', skill)
            if skill_name:
                supply_counter[skill_name.strip()] += 1
    
    # Get all unique skills
    all_skills = set(demand_counter.keys()) | set(supply_counter.keys())
    
    # Calculate metrics for each skill
    skill_metrics = []
    for skill in all_skills:
        demand = demand_counter.get(skill, 0)
        supply = supply_counter.get(skill, 0)
        
        # Calculate demand/supply ratio (higher = more valuable)
        if supply == 0:
            ratio = float('inf') if demand > 0 else 0
            ratio_display = "∞" if demand > 0 else "0"
        else:
            ratio = demand / supply
            ratio_display = f"{ratio:.1f}x"
        
        # Determine market status
        if supply == 0 and demand > 0:
            status = "critical_shortage"
            status_label = "Critical shortage"
        elif ratio > 2:
            status = "high_demand"
            status_label = "High demand"
        elif ratio > 1:
            status = "balanced"
            status_label = "Balanced"
        elif ratio > 0.5:
            status = "competitive"
            status_label = "Competitive"
        else:
            status = "oversupplied"
            status_label = "Oversupplied"
        
        skill_metrics.append({
            'skill': skill,
            'demand': demand,
            'supply': supply,
            'ratio': ratio if ratio != float('inf') else 999,
            'ratio_display': ratio_display,
            'status': status,
            'status_label': status_label,
        })
    
    # Sort by demand (most sought-after first)
    skill_metrics.sort(key=lambda x: (-x['demand'], -x['ratio']))
    
    return {
        'total_active_projects': len(projects.data or []),
        'total_founders': len(founders.data or []),
        'total_unique_skills': len(all_skills),
        'skills': skill_metrics,
    }


def get_user_skill_insights(clerk_user_id: str) -> Dict[str, Any]:
    """
    Get personalized skill market insights for a user.
    
    Returns:
    - How their skills compare to market demand
    - Which of their skills are most valuable
    - Suggestions for skills to highlight
    
    Gated by plan:
    - FREE: Basic teaser (overall position only)
    - PRO: Full breakdown with actionable insights
    """
    from services import plan_service
    
    supabase = get_supabase()
    
    # Get user's profile
    user = supabase.table('founders').select('id, skills, name').eq(
        'clerk_user_id', clerk_user_id
    ).execute()
    
    if not user.data:
        raise ValueError("Profile not found")
    
    user_data = user.data[0]
    user_skills = user_data.get('skills') or []
    
    # Normalize user skills
    user_skill_names = set()
    for skill in user_skills:
        skill_name = skill if isinstance(skill, str) else skill.get('skill', skill)
        if skill_name:
            user_skill_names.add(skill_name.strip())
    
    if not user_skill_names:
        return {
            'has_skills': False,
            'message': 'Add skills to your profile to see market insights',
        }
    
    # Get market data
    market_data = get_skill_market_data()
    skill_lookup = {s['skill']: s for s in market_data['skills']}
    
    # Analyze user's skills
    user_skill_analysis = []
    total_demand_score = 0
    high_demand_count = 0
    
    for skill_name in user_skill_names:
        skill_data = skill_lookup.get(skill_name)
        if skill_data:
            user_skill_analysis.append({
                'skill': skill_name,
                'demand': skill_data['demand'],
                'supply': skill_data['supply'],
                'ratio': skill_data['ratio'],
                'ratio_display': skill_data['ratio_display'],
                'status': skill_data['status'],
                'status_label': skill_data['status_label'],
            })
            total_demand_score += skill_data['demand']
            if skill_data['status'] in ('critical_shortage', 'high_demand'):
                high_demand_count += 1
        else:
            # Skill not tracked yet (no projects need it)
            user_skill_analysis.append({
                'skill': skill_name,
                'demand': 0,
                'supply': 1,
                'ratio': 0,
                'ratio_display': '0x',
                'status': 'not_tracked',
                'status_label': 'No current demand',
            })
    
    # Sort by demand (most valuable first)
    user_skill_analysis.sort(key=lambda x: (-x['demand'], -x['ratio']))
    
    # Calculate percentile (how user compares to other founders)
    # Get all founders' total demand scores
    all_founders = supabase.table('founders').select('skills').execute()
    founder_scores = []
    for founder in (all_founders.data or []):
        f_skills = founder.get('skills') or []
        f_score = 0
        for skill in f_skills:
            skill_name = skill if isinstance(skill, str) else skill.get('skill', skill)
            if skill_name and skill_name.strip() in skill_lookup:
                f_score += skill_lookup[skill_name.strip()]['demand']
        founder_scores.append(f_score)
    
    founder_scores.sort()
    if founder_scores:
        # Find percentile
        rank = sum(1 for s in founder_scores if s < total_demand_score)
        percentile = int((rank / len(founder_scores)) * 100)
    else:
        percentile = 50
    
    # Generate insights
    if percentile >= 80:
        position_label = "Highly sought-after"
        position_message = f"Your skills are in the top {100 - percentile}% of demand on Guild Space"
    elif percentile >= 60:
        position_label = "Above average"
        position_message = f"Your skills are more in-demand than {percentile}% of founders"
    elif percentile >= 40:
        position_label = "Average demand"
        position_message = "Your skills match typical market demand"
    else:
        position_label = "Niche positioning"
        position_message = "Consider highlighting additional skills to improve visibility"
    
    # Get user's plan
    plan_config = plan_service.get_founder_plan(clerk_user_id)
    user_plan = plan_config.get('id', 'FREE')
    
    # Build response based on plan
    if user_plan == 'FREE':
        # Teaser for FREE users
        return {
            'has_skills': True,
            'preview_locked': True,
            'skill_count': len(user_skill_names),
            'percentile': percentile,
            'position_label': position_label,
            'position_message': position_message,
            'high_demand_skills_count': high_demand_count,
            'teaser': f"You have {high_demand_count} high-demand skill{'s' if high_demand_count != 1 else ''} — upgrade to see which ones",
            'upgrade_message': 'Unlock full skill analysis with Pro',
        }
    
    # Full data for PRO users
    # Find top skills they're missing (high demand, user doesn't have)
    missing_opportunities = []
    for skill_data in market_data['skills'][:20]:  # Top 20 in demand
        if skill_data['skill'] not in user_skill_names:
            if skill_data['status'] in ('critical_shortage', 'high_demand'):
                missing_opportunities.append({
                    'skill': skill_data['skill'],
                    'demand': skill_data['demand'],
                    'status_label': skill_data['status_label'],
                })
    
    # Generate actionable advice
    advice = []
    if high_demand_count > 0:
        top_skill = user_skill_analysis[0]
        advice.append(f"Lead with '{top_skill['skill']}' in your profile — it's your most valuable skill")
    
    if missing_opportunities:
        advice.append(f"Consider adding '{missing_opportunities[0]['skill']}' if you have experience — it's in high demand")
    
    if any(s['status'] == 'oversupplied' for s in user_skill_analysis):
        oversupplied = [s for s in user_skill_analysis if s['status'] == 'oversupplied']
        advice.append(f"'{oversupplied[0]['skill']}' is common — pair it with specific experience to stand out")
    
    return {
        'has_skills': True,
        'preview_locked': False,
        'skill_count': len(user_skill_names),
        'percentile': percentile,
        'position_label': position_label,
        'position_message': position_message,
        'high_demand_skills_count': high_demand_count,
        'skills': user_skill_analysis,
        'missing_opportunities': missing_opportunities[:5],
        'advice': advice,
        'market_summary': {
            'total_projects_seeking': market_data['total_active_projects'],
            'total_founders': market_data['total_founders'],
        }
    }


def get_top_skills_overview(limit: int = 10) -> Dict[str, Any]:
    """
    Get a quick overview of top skills in demand.
    This is a lighter endpoint for display on dashboards.
    """
    market_data = get_skill_market_data()
    
    # Top demanded skills
    top_demand = [s for s in market_data['skills'] if s['demand'] > 0][:limit]
    
    # Skills with critical shortage
    critical = [s for s in market_data['skills'] if s['status'] == 'critical_shortage'][:5]
    
    # Most competitive (oversupplied)
    competitive = [s for s in market_data['skills'] if s['status'] == 'oversupplied'][:5]
    
    return {
        'top_in_demand': top_demand,
        'critical_shortage': critical,
        'most_competitive': competitive,
        'total_projects': market_data['total_active_projects'],
        'total_founders': market_data['total_founders'],
    }
