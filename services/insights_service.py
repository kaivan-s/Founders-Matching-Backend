"""Project Insights Service - AI-powered idea validation and competition analysis"""
import os
import json
import time
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
import httpx
from config.database import get_supabase
from utils.logger import log_info, log_error, log_warning

# Perplexity API configuration
PERPLEXITY_API_KEY = os.environ.get('PERPLEXITY_API_KEY')
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar-pro"  # Best for research with web search

# Monthly usage limits per tier
INSIGHTS_LIMITS = {
    'FREE': 0,      # No access
    'PRO': 3,       # 3 reports per month
    'PRO_PLUS': 10  # 10 reports per month
}


def _get_founder_id(clerk_user_id: str) -> str:
    """Helper to get founder ID from clerk_user_id"""
    supabase = get_supabase()
    user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not user_profile.data:
        raise ValueError("Founder profile not found")
    return user_profile.data[0]['id']


def _get_founder_plan(clerk_user_id: str) -> str:
    """Get founder's current plan tier"""
    from services import plan_service
    plan_config = plan_service.get_founder_plan(clerk_user_id)
    return plan_config.get('id', 'FREE')


def check_insights_access(clerk_user_id: str) -> Tuple[bool, int, int, str]:
    """
    Check if user can generate insights.
    Returns: (can_generate, current_usage, max_allowed, tier)
    """
    plan = _get_founder_plan(clerk_user_id)
    max_allowed = INSIGHTS_LIMITS.get(plan, 0)
    
    if max_allowed == 0:
        return (False, 0, 0, plan)
    
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get current month usage
    month_year = datetime.now(timezone.utc).strftime('%Y-%m')
    usage = supabase.table('insights_usage').select('usage_count').eq(
        'founder_id', founder_id
    ).eq('month_year', month_year).execute()
    
    current_usage = usage.data[0].get('usage_count', 0) if usage.data else 0
    can_generate = current_usage < max_allowed
    
    return (can_generate, current_usage, max_allowed, plan)


def _increment_usage(founder_id: str) -> None:
    """Increment monthly insights usage count"""
    supabase = get_supabase()
    month_year = datetime.now(timezone.utc).strftime('%Y-%m')
    
    # Try upsert with increment
    existing = supabase.table('insights_usage').select('id, usage_count').eq(
        'founder_id', founder_id
    ).eq('month_year', month_year).execute()
    
    if existing.data:
        new_count = existing.data[0].get('usage_count', 0) + 1
        supabase.table('insights_usage').update({
            'usage_count': new_count
        }).eq('id', existing.data[0]['id']).execute()
    else:
        supabase.table('insights_usage').insert({
            'founder_id': founder_id,
            'month_year': month_year,
            'usage_count': 1
        }).execute()


def get_project_insights(clerk_user_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    """Get existing insights for a project"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Verify project belongs to user
    project = supabase.table('projects').select('id, founder_id').eq('id', project_id).execute()
    if not project.data:
        raise ValueError("Project not found")
    
    if project.data[0]['founder_id'] != founder_id:
        raise ValueError("Access denied: Project does not belong to you")
    
    # Get insights
    insights = supabase.table('project_insights').select('*').eq('project_id', project_id).execute()
    
    if not insights.data:
        return None
    
    return insights.data[0]


def get_insights_for_workspace(clerk_user_id: str, workspace_id: str) -> Optional[Dict[str, Any]]:
    """Get insights for a project associated with a workspace"""
    supabase = get_supabase()
    
    # Get workspace and verify access
    workspace = supabase.table('workspaces').select('id, project_id').eq('id', workspace_id).execute()
    if not workspace.data:
        raise ValueError("Workspace not found")
    
    project_id = workspace.data[0].get('project_id')
    if not project_id:
        return None
    
    # Get insights for this project
    insights = supabase.table('project_insights').select('*').eq('project_id', project_id).execute()
    
    if not insights.data:
        return None
    
    return insights.data[0]


def _build_analysis_prompt(project: Dict[str, Any]) -> str:
    """Build the prompt for Perplexity API"""
    title = project.get('title', 'Untitled Project')
    description = project.get('description', '')
    stage = project.get('stage', 'idea')
    genre = project.get('genre', 'technology')
    needed_skills = project.get('needed_skills', [])
    
    skills_text = ', '.join(needed_skills) if needed_skills else 'various skills'
    
    stage_descriptions = {
        'idea': 'early idea stage',
        'mvp': 'MVP development stage',
        'early-stage': 'early stage with some traction',
        'growth': 'growth stage'
    }
    stage_text = stage_descriptions.get(stage, stage)
    
    prompt = f"""Analyze this startup idea and provide a comprehensive validation report:

**Project Title:** {title}

**Description:** {description}

**Stage:** {stage_text}

**Industry/Genre:** {genre}

**Skills Needed:** {skills_text}

Please provide a detailed analysis with the following sections. Be specific, data-driven, and actionable:

1. **Executive Summary** (3-4 sentences)
   - Quick overview of the idea's market potential and key findings

2. **Market Overview**
   - Market size estimates (TAM/SAM/SOM if applicable)
   - Growth trends and projections
   - Key market drivers and tailwinds

3. **Competitor Landscape**
   - List 5-8 direct and indirect competitors
   - For each competitor include: name, brief description, funding status if known, key strengths
   - Note any gaps in the market

4. **Competitive Positioning**
   - Where this idea fits in the market
   - Differentiation opportunities
   - Potential unique advantages the founders could leverage

5. **SWOT Analysis**
   - Strengths: Internal advantages
   - Weaknesses: Internal challenges
   - Opportunities: External factors to capitalize on
   - Threats: External risks to watch

6. **Key Risks & Challenges**
   - 3-5 main risks to be aware of
   - Include market, technical, and execution risks

7. **Recommendations**
   - 3-5 actionable next steps
   - Prioritized focus areas for validation

Format your response as a valid JSON object with this exact structure:
{{
  "executive_summary": "string",
  "market_overview": {{
    "market_size": "string",
    "growth_trends": "string",
    "key_drivers": ["string"]
  }},
  "competitors": [
    {{
      "name": "string",
      "description": "string",
      "funding": "string or null",
      "strengths": ["string"]
    }}
  ],
  "positioning": {{
    "market_fit": "string",
    "differentiation": ["string"],
    "unique_advantages": ["string"]
  }},
  "swot": {{
    "strengths": ["string"],
    "weaknesses": ["string"],
    "opportunities": ["string"],
    "threats": ["string"]
  }},
  "risks": [
    {{
      "type": "market|technical|execution",
      "description": "string",
      "mitigation": "string"
    }}
  ],
  "recommendations": [
    {{
      "priority": 1-5,
      "action": "string",
      "rationale": "string"
    }}
  ]
}}

Return ONLY the JSON object, no additional text or markdown formatting."""

    return prompt


def _call_perplexity_api(prompt: str) -> Tuple[Dict[str, Any], int, int]:
    """
    Call Perplexity API and return (response_data, tokens_used, time_ms)
    """
    if not PERPLEXITY_API_KEY:
        raise ValueError("Perplexity API key not configured")
    
    start_time = time.time()
    
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a startup advisor and market research expert. Analyze startup ideas and provide actionable, data-driven insights. Always respond with valid JSON only."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.2,  # Lower temperature for more consistent, factual responses
        "max_tokens": 4000,
        "return_citations": False,
        "search_domain_filter": [],
        "search_recency_filter": "month"  # Focus on recent data
    }
    
    with httpx.Client(timeout=60.0) as client:
        response = client.post(PERPLEXITY_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    # Extract content from response
    content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
    
    # Extract token usage
    usage = result.get('usage', {})
    total_tokens = usage.get('total_tokens', 0)
    
    # Parse JSON from content
    try:
        # Clean up response - remove markdown code blocks if present
        content = content.strip()
        if content.startswith('```json'):
            content = content[7:]
        if content.startswith('```'):
            content = content[3:]
        if content.endswith('```'):
            content = content[:-3]
        content = content.strip()
        
        report_data = json.loads(content)
    except json.JSONDecodeError as e:
        log_error(f"Failed to parse Perplexity response as JSON: {e}")
        log_error(f"Raw content: {content[:500]}...")
        raise ValueError(f"Failed to parse AI response: {str(e)}")
    
    return (report_data, total_tokens, elapsed_ms)


def generate_project_insights(clerk_user_id: str, project_id: str) -> Dict[str, Any]:
    """
    Generate AI insights for a project.
    Returns the insights record.
    """
    # Check access and limits
    can_generate, current_usage, max_allowed, tier = check_insights_access(clerk_user_id)
    
    if tier == 'FREE':
        raise ValueError("Insights generation requires a Pro or Pro+ subscription. Upgrade to access this feature.")
    
    if not can_generate:
        raise ValueError(f"Monthly insights limit reached ({current_usage}/{max_allowed}). Your limit resets next month.")
    
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    # Get project details
    project = supabase.table('projects').select('*').eq('id', project_id).execute()
    if not project.data:
        raise ValueError("Project not found")
    
    project_data = project.data[0]
    
    # Verify ownership
    if project_data['founder_id'] != founder_id:
        raise ValueError("Access denied: Project does not belong to you")
    
    # Check if insights already exist
    existing = supabase.table('project_insights').select('id, status').eq('project_id', project_id).execute()
    
    if existing.data:
        insights_id = existing.data[0]['id']
        # Update status to generating
        supabase.table('project_insights').update({
            'status': 'generating',
            'error_message': None
        }).eq('id', insights_id).execute()
    else:
        # Create new insights record
        result = supabase.table('project_insights').insert({
            'project_id': project_id,
            'founder_id': founder_id,
            'status': 'generating'
        }).execute()
        insights_id = result.data[0]['id']
    
    try:
        # Build prompt and call API
        prompt = _build_analysis_prompt(project_data)
        report_data, tokens_used, generation_time_ms = _call_perplexity_api(prompt)
        
        # Update with results
        supabase.table('project_insights').update({
            'status': 'completed',
            'report_data': report_data,
            'model_used': PERPLEXITY_MODEL,
            'tokens_used': tokens_used,
            'generation_time_ms': generation_time_ms,
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'error_message': None
        }).eq('id', insights_id).execute()
        
        # Increment usage (only on success)
        _increment_usage(founder_id)
        
        log_info(f"Generated insights for project {project_id} in {generation_time_ms}ms using {tokens_used} tokens")
        
    except Exception as e:
        error_msg = str(e)
        log_error(f"Failed to generate insights for project {project_id}: {error_msg}")
        log_error(traceback.format_exc())
        
        # Update with error
        supabase.table('project_insights').update({
            'status': 'failed',
            'error_message': error_msg
        }).eq('id', insights_id).execute()
        
        raise ValueError(f"Failed to generate insights: {error_msg}")
    
    # Return updated insights
    return get_project_insights(clerk_user_id, project_id)


def get_insights_usage(clerk_user_id: str) -> Dict[str, Any]:
    """Get current insights usage and limits for a user"""
    can_generate, current_usage, max_allowed, tier = check_insights_access(clerk_user_id)
    
    return {
        'tier': tier,
        'can_generate': can_generate,
        'current_usage': current_usage,
        'max_allowed': max_allowed,
        'remaining': max(0, max_allowed - current_usage) if max_allowed > 0 else 0,
        'resets_at': _get_next_month_start().isoformat()
    }


def _get_next_month_start() -> datetime:
    """Get the start of next month (when usage resets)"""
    now = datetime.now(timezone.utc)
    if now.month == 12:
        return datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
