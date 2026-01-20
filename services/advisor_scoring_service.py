"""Advisor scoring service for calculating advisor performance scores after 90 days"""
from config.database import get_supabase
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import json

def calculate_advisor_score(clerk_user_id: str, workspace_id: str, advisor_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Calculate comprehensive advisor score after 90 days based on service quality.
    
    Scoring components (weighted):
    1. Weekly Checkin Completion (25%): Regular, on-time checkins
    2. Comment Quality & Engagement (25%): Quality and frequency of comments
    3. Response Time (15%): How quickly advisor responds to checkins/tasks
    4. Task & KPI Impact (20%): Improvement in task completion and KPI progress
    5. Founder Ratings (15%): Direct feedback and ratings from founders
    
    Returns score out of 100 for payment calculation.
    """
    supabase = get_supabase()
    
    # Get advisor user_id if not provided
    if not advisor_user_id:
        advisor_participant = supabase.table('workspace_participants').select(
            'user_id, joined_at'
        ).eq('workspace_id', workspace_id).eq('role', 'ADVISOR').execute()
        
        if not advisor_participant.data:
            raise ValueError("No advisor found in this workspace")
        
        advisor_user_id = advisor_participant.data[0]['user_id']
        advisor_joined_at = advisor_participant.data[0].get('joined_at')
    else:
        advisor_participant = supabase.table('workspace_participants').select('joined_at').eq(
            'workspace_id', workspace_id
        ).eq('user_id', advisor_user_id).eq('role', 'ADVISOR').execute()
        
        if not advisor_participant.data:
            raise ValueError("Advisor not found in this workspace")
        
        advisor_joined_at = advisor_participant.data[0].get('joined_at')
    
    # Parse advisor joined date
    if advisor_joined_at:
        if isinstance(advisor_joined_at, str):
            advisor_joined_at = datetime.fromisoformat(advisor_joined_at.replace('Z', '+00:00'))
        if advisor_joined_at.tzinfo is None:
            advisor_joined_at = advisor_joined_at.replace(tzinfo=timezone.utc)
    else:
        raise ValueError("Advisor join date not found")
    
    now = datetime.now(timezone.utc)
    days_active = (now - advisor_joined_at).days
    
    # Only calculate score if advisor has been active for at least 90 days
    if days_active < 90:
        return {
            'can_calculate': False,
            'days_active': days_active,
            'days_remaining': 90 - days_active,
            'message': f'Advisor has been active for {days_active} days. Score calculation available after 90 days.'
        }
    
    # Calculate for 90-day period
    period_start = advisor_joined_at
    period_end = advisor_joined_at + timedelta(days=90)
    review_period_days = 90
    
    # ===== 1. WEEKLY CHECKIN COMPLETION (25%) =====
    # Check how many checkins advisor reviewed/responded to
    checkins = supabase.table('workspace_checkins').select(
        'id, week_start, created_at'
    ).eq('workspace_id', workspace_id).gte(
        'created_at', period_start.isoformat()
    ).lte('created_at', period_end.isoformat()).execute()
    
    # Get advisor responses to checkins (comments, verdicts, reviews)
    advisor_responses = set()
    total_checkins = len(checkins.data or [])
    
    if total_checkins > 0:
        checkin_ids = [c['id'] for c in checkins.data]
        
        # Check for comments by advisor
        comments = supabase.table('workspace_checkin_comments').select('checkin_id').in_(
            'checkin_id', checkin_ids
        ).eq('user_id', advisor_user_id).execute()
        
        # Check for verdicts by advisor
        verdicts = supabase.table('workspace_checkin_verdicts').select('checkin_id').in_(
            'checkin_id', checkin_ids
        ).eq('user_id', advisor_user_id).execute()
        
        # Check for partner reviews by advisor
        reviews = supabase.table('workspace_checkin_partner_reviews').select('checkin_id').in_(
            'checkin_id', checkin_ids
        ).eq('user_id', advisor_user_id).execute()
        
        # Combine all responses
        for c in (comments.data or []):
            advisor_responses.add(c['checkin_id'])
        for v in (verdicts.data or []):
            advisor_responses.add(v['checkin_id'])
        for r in (reviews.data or []):
            advisor_responses.add(r['checkin_id'])
    
    checkin_response_rate = (len(advisor_responses) / total_checkins * 100) if total_checkins > 0 else 0
    checkin_score = min(100, checkin_response_rate)  # 25% weight applied later
    
    # ===== 2. COMMENT QUALITY & ENGAGEMENT (25%) =====
    # Analyze comment quality: length, frequency, depth
    comments = supabase.table('workspace_checkin_comments').select(
        'id, comment, created_at, checkin_id'
    ).eq('user_id', advisor_user_id).gte(
        'created_at', period_start.isoformat()
    ).lte('created_at', period_end.isoformat()).execute()
    
    total_comments = len(comments.data or [])
    total_characters = sum(len(c.get('comment', '')) for c in (comments.data or []))
    avg_comment_length = total_characters / total_checkins if total_checkins > 0 else 0
    
    # Quality thresholds
    # - Minimum 50 chars per comment for quality engagement
    # - At least 1 comment per 2 checkins
    quality_comment_count = sum(1 for c in (comments.data or []) if len(c.get('comment', '')) >= 50)
    quality_comment_ratio = (quality_comment_count / total_checkins * 100) if total_checkins > 0 else 0
    comment_frequency_score = min(100, (total_comments / max(1, total_checkins / 2)) * 100)
    
    comment_quality_score = (
        (quality_comment_ratio * 0.6) +  # 60% weight on quality comments
        (comment_frequency_score * 0.4)   # 40% weight on frequency
    )  # 25% weight applied later
    
    # ===== 3. RESPONSE TIME (15%) =====
    # Calculate average response time to checkins (time from checkin creation to advisor response)
    response_times = []
    
    if checkins.data:
        for checkin in checkins.data:
            checkin_created = datetime.fromisoformat(checkin['created_at'].replace('Z', '+00:00'))
            if checkin_created.tzinfo is None:
                checkin_created = checkin_created.replace(tzinfo=timezone.utc)
            
            checkin_id = checkin['id']
            
            # Find earliest advisor response to this checkin
            earliest_response = None
            
            # Check comments
            checkin_comments = supabase.table('workspace_checkin_comments').select('created_at').eq(
                'checkin_id', checkin_id
            ).eq('user_id', advisor_user_id).order('created_at', desc=False).limit(1).execute()
            
            if checkin_comments.data:
                comment_time = datetime.fromisoformat(checkin_comments.data[0]['created_at'].replace('Z', '+00:00'))
                if comment_time.tzinfo is None:
                    comment_time = comment_time.replace(tzinfo=timezone.utc)
                earliest_response = comment_time
            
            # Check verdicts
            checkin_verdicts = supabase.table('workspace_checkin_verdicts').select('created_at').eq(
                'checkin_id', checkin_id
            ).eq('user_id', advisor_user_id).order('created_at', desc=False).limit(1).execute()
            
            if checkin_verdicts.data:
                verdict_time = datetime.fromisoformat(checkin_verdicts.data[0]['created_at'].replace('Z', '+00:00'))
                if verdict_time.tzinfo is None:
                    verdict_time = verdict_time.replace(tzinfo=timezone.utc)
                if earliest_response is None or verdict_time < earliest_response:
                    earliest_response = verdict_time
            
            # Check reviews
            checkin_reviews = supabase.table('workspace_checkin_partner_reviews').select('created_at').eq(
                'checkin_id', checkin_id
            ).eq('user_id', advisor_user_id).order('created_at', desc=False).limit(1).execute()
            
            if checkin_reviews.data:
                review_time = datetime.fromisoformat(checkin_reviews.data[0]['created_at'].replace('Z', '+00:00'))
                if review_time.tzinfo is None:
                    review_time = review_time.replace(tzinfo=timezone.utc)
                if earliest_response is None or review_time < earliest_response:
                    earliest_response = review_time
            
            if earliest_response:
                response_hours = (earliest_response - checkin_created).total_seconds() / 3600
                response_times.append(response_hours)
    
    avg_response_hours = sum(response_times) / len(response_times) if response_times else None
    
    # Score based on response time (48 hours = 100, 7 days = 0)
    if avg_response_hours is not None:
        # Linear scaling: 48h = 100, 168h (7 days) = 0
        if avg_response_hours <= 48:
            response_time_score = 100
        elif avg_response_hours >= 168:
            response_time_score = 0
        else:
            response_time_score = 100 - ((avg_response_hours - 48) / (168 - 48) * 100)
    else:
        response_time_score = 0  # 15% weight applied later
    
    # ===== 4. TASK & KPI IMPACT (20%) =====
    # Similar to partner impact scorecard but simplified for scoring
    tasks = supabase.table('workspace_tasks').select(
        'id, created_at, completed_at, status, kpi_id, decision_id'
    ).eq('workspace_id', workspace_id).gte(
        'created_at', period_start.isoformat()
    ).lte('created_at', period_end.isoformat()).execute()
    
    important_tasks = [t for t in (tasks.data or []) if (t.get('kpi_id') or t.get('decision_id'))]
    completed_important_tasks = [t for t in important_tasks if t.get('status') == 'DONE']
    
    task_completion_rate = (
        len(completed_important_tasks) / len(important_tasks) * 100
    ) if important_tasks else 0
    
    # KPI progress (simplified - use status-based progress)
    kpis = supabase.table('workspace_kpis').select(
        'id, status, created_at'
    ).eq('workspace_id', workspace_id).gte(
        'created_at', period_start.isoformat()
    ).lte('created_at', period_end.isoformat()).execute()
    
    kpi_statuses = {'not_started': 0, 'in_progress': 50, 'done': 100}
    kpi_scores = [kpi_statuses.get(kpi.get('status', 'not_started'), 0) for kpi in (kpis.data or [])]
    avg_kpi_progress = sum(kpi_scores) / len(kpi_scores) if kpi_scores else 0
    
    # Combined task and KPI impact score
    impact_score = (
        (task_completion_rate * 0.6) +  # 60% weight on tasks
        (avg_kpi_progress * 0.4)         # 40% weight on KPIs
    )  # 20% weight applied later
    
    # ===== 5. FOUNDER RATINGS (15%) =====
    # Get quarterly reviews and any additional ratings
    quarterly_reviews = supabase.table('quarterly_reviews').select(
        'value_rating, quarter, created_at'
    ).eq('workspace_id', workspace_id).eq(
        'partner_user_id', advisor_user_id
    ).gte('created_at', period_start.isoformat()).lte(
        'created_at', period_end.isoformat()
    ).execute()
    
    ratings = [r.get('value_rating') for r in (quarterly_reviews.data or []) if r.get('value_rating')]
    
    if ratings:
        avg_rating = sum(ratings) / len(ratings)
        # Convert 1-5 rating to 0-100 score
        rating_score = (avg_rating / 5) * 100
    else:
        rating_score = 50  # Neutral score if no ratings yet  # 15% weight applied later
    
    # ===== CALCULATE FINAL SCORE =====
    final_score = (
        (checkin_score * 0.25) +           # 25% - Checkin completion
        (comment_quality_score * 0.25) +   # 25% - Comment quality
        (response_time_score * 0.15) +     # 15% - Response time
        (impact_score * 0.20) +            # 20% - Task & KPI impact
        (rating_score * 0.15)              # 15% - Founder ratings
    )
    
    return {
        'can_calculate': True,
        'advisor_user_id': advisor_user_id,
        'workspace_id': workspace_id,
        'days_active': days_active,
        'review_period_start': period_start.isoformat(),
        'review_period_end': period_end.isoformat(),
        'final_score': round(final_score, 2),
        'component_scores': {
            'checkin_completion': {
                'score': round(checkin_score, 2),
                'weight': 0.25,
                'weighted_score': round(checkin_score * 0.25, 2),
                'details': {
                    'total_checkins': total_checkins,
                    'advisor_responses': len(advisor_responses),
                    'response_rate': round(checkin_response_rate, 2)
                }
            },
            'comment_quality': {
                'score': round(comment_quality_score, 2),
                'weight': 0.25,
                'weighted_score': round(comment_quality_score * 0.25, 2),
                'details': {
                    'total_comments': total_comments,
                    'quality_comments': quality_comment_count,
                    'avg_comment_length': round(avg_comment_length, 0),
                    'comment_frequency_score': round(comment_frequency_score, 2)
                }
            },
            'response_time': {
                'score': round(response_time_score, 2),
                'weight': 0.15,
                'weighted_score': round(response_time_score * 0.15, 2),
                'details': {
                    'avg_response_hours': round(avg_response_hours, 2) if avg_response_hours else None,
                    'total_responses_timed': len(response_times)
                }
            },
            'task_kpi_impact': {
                'score': round(impact_score, 2),
                'weight': 0.20,
                'weighted_score': round(impact_score * 0.20, 2),
                'details': {
                    'task_completion_rate': round(task_completion_rate, 2),
                    'avg_kpi_progress': round(avg_kpi_progress, 2),
                    'important_tasks_total': len(important_tasks),
                    'important_tasks_completed': len(completed_important_tasks)
                }
            },
            'founder_ratings': {
                'score': round(rating_score, 2),
                'weight': 0.15,
                'weighted_score': round(rating_score * 0.15, 2),
                'details': {
                    'total_ratings': len(ratings),
                    'avg_rating': round(avg_rating, 2) if ratings else None
                }
            }
        },
        'payment_recommendation': {
            'base_rate_multiplier': final_score / 100,  # 0.0 to 1.0
            'recommended_payment_percentage': round((final_score / 100) * 100, 2)
        }
    }


def save_advisor_score(score_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save calculated advisor score to database.
    
    Args:
        score_data: Result from calculate_advisor_score() function
        
    Returns:
        Saved score record from database
    """
    if not score_data.get('can_calculate'):
        raise ValueError("Cannot save score: Score calculation not available yet")
    
    supabase = get_supabase()
    
    # Prepare data for insertion
    score_record = {
        'advisor_user_id': score_data['advisor_user_id'],
        'workspace_id': score_data['workspace_id'],
        'final_score': float(score_data['final_score']),
        'days_active': score_data['days_active'],
        'period_start': score_data['review_period_start'],
        'period_end': score_data['review_period_end'],
        'component_scores': json.dumps(score_data['component_scores']),
        'payment_recommendation': json.dumps(score_data['payment_recommendation']),
        'calculated_at': datetime.now(timezone.utc).isoformat(),
    }
    
    # Check if score already exists for this period
    existing = supabase.table('advisor_scores').select('id').eq(
        'advisor_user_id', score_data['advisor_user_id']
    ).eq('workspace_id', score_data['workspace_id']).eq(
        'period_start', score_data['review_period_start']
    ).eq('period_end', score_data['review_period_end']).execute()
    
    if existing.data:
        # Update existing record
        result = supabase.table('advisor_scores').update({
            'final_score': score_record['final_score'],
            'days_active': score_record['days_active'],
            'component_scores': score_record['component_scores'],
            'payment_recommendation': score_record['payment_recommendation'],
            'calculated_at': score_record['calculated_at'],
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', existing.data[0]['id']).execute()
    else:
        # Insert new record
        result = supabase.table('advisor_scores').insert(score_record).execute()
    
    if not result.data:
        raise ValueError("Failed to save advisor score")
    
    return result.data[0]


def get_advisor_scores(advisor_user_id: str, workspace_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Retrieve stored advisor scores from database.
    
    Args:
        advisor_user_id: Advisor's user ID
        workspace_id: Optional workspace ID to filter by
        limit: Maximum number of scores to return
        
    Returns:
        List of score records, ordered by calculated_at DESC
    """
    supabase = get_supabase()
    
    query = supabase.table('advisor_scores').select('*').eq(
        'advisor_user_id', advisor_user_id
    )
    
    if workspace_id:
        query = query.eq('workspace_id', workspace_id)
    
    result = query.order('calculated_at', desc=True).limit(limit).execute()
    
    if not result.data:
        return []
    
    # Parse JSONB fields back to Python dicts
    scores = []
    for record in result.data:
        score = dict(record)
        if isinstance(score.get('component_scores'), str):
            score['component_scores'] = json.loads(score['component_scores'])
        if isinstance(score.get('payment_recommendation'), str):
            score['payment_recommendation'] = json.loads(score['payment_recommendation'])
        scores.append(score)
    
    return scores


def get_latest_advisor_score(advisor_user_id: str, workspace_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the most recent advisor score for a specific workspace.
    
    Args:
        advisor_user_id: Advisor's user ID
        workspace_id: Workspace ID
        
    Returns:
        Latest score record or None if not found
    """
    scores = get_advisor_scores(advisor_user_id, workspace_id, limit=1)
    return scores[0] if scores else None


def calculate_and_save_advisor_score(clerk_user_id: str, workspace_id: str, advisor_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Calculate advisor score and save it to database.
    
    Args:
        clerk_user_id: Clerk user ID of the requester
        workspace_id: Workspace ID
        advisor_user_id: Optional advisor user ID (if None, uses active advisor)
        
    Returns:
        Saved score record with calculation details
    """
    # Calculate score
    score_data = calculate_advisor_score(clerk_user_id, workspace_id, advisor_user_id)
    
    # Save if calculation was successful
    if score_data.get('can_calculate'):
        saved_score = save_advisor_score(score_data)
        score_data['saved'] = True
        score_data['saved_score_id'] = saved_score['id']
        score_data['saved_at'] = saved_score['calculated_at']
    else:
        score_data['saved'] = False
    
    return score_data
