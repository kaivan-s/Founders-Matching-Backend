"""Compatibility scoring and matching service"""

def calculate_match_score(user_profile, other_profile, user_project=None, other_project=None):
    """
    Calculate compatibility score between two founders/projects
    
    Args:
        user_profile: Current user's profile
        other_profile: Other founder's profile
        user_project: Optional project object with project-specific compatibility_answers
        other_project: Optional project object with project-specific compatibility_answers
    
    Returns:
        dict: {
            'overall_score': 85,  # 0-100
            'breakdown': {
                'skills': {'score': 90, 'reason': '...'},
                'vision': {'score': 85, 'reason': '...'},
                'work_style': {'score': 75, 'reason': '...'},
                'location': {'score': 80, 'reason': '...'},
                'project_stage': {'score': 90, 'reason': '...'},
            }
        }
    """
    # Use project-specific compatibility answers only (project-wise storage)
    user_answers = (user_project.get('compatibility_answers') or {}) if user_project else {}
    other_answers = (other_project.get('compatibility_answers') or {}) if other_project else {}
    
    breakdown = {}
    
    # 1. Skills Complementarity (30% weight)
    skills_score = calculate_skills_score(user_profile, other_profile)
    breakdown['skills'] = skills_score
    
    # 2. Vision Alignment (25% weight)
    vision_score = calculate_vision_score(user_answers, other_answers)
    breakdown['vision'] = vision_score
    
    # 3. Work Style Compatibility (20% weight)
    work_style_score = calculate_work_style_score(user_answers, other_answers)
    breakdown['work_style'] = work_style_score
    
    # 4. Project Stage Match (15% weight)
    stage_score = calculate_stage_score(user_profile, other_profile)
    breakdown['project_stage'] = stage_score
    
    # 5. Location Proximity (10% weight)
    location_score = calculate_location_score(user_profile, other_profile)
    breakdown['location'] = location_score
    
    # Calculate weighted overall score
    overall_score = (
        skills_score['score'] * 0.30 +
        vision_score['score'] * 0.25 +
        work_style_score['score'] * 0.20 +
        stage_score['score'] * 0.15 +
        location_score['score'] * 0.10
    )
    
    return {
        'overall_score': round(overall_score),
        'breakdown': breakdown
    }

def calculate_skills_score(user, other):
    """Calculate skills complementarity - do they fill each other's gaps?"""
    user_skills = set(user.get('skills') or [])
    other_skills = set(other.get('skills') or [])
    user_looking = (user.get('looking_for') or '').lower()
    other_looking = (other.get('looking_for') or '').lower()
    
    if not user_skills or not other_skills:
        return {'score': 50, 'reason': 'Limited skill information'}
    
    # Check if they complement each other (different skills)
    overlap = len(user_skills & other_skills)
    unique_to_other = len(other_skills - user_skills)
    unique_to_user = len(user_skills - other_skills)
    
    # Perfect complement: minimal overlap, lots of unique skills
    total_unique = unique_to_other + unique_to_user
    if total_unique == 0:
        score = 40  # Same skills = not complementary
        reason = 'Too much skill overlap - you need different expertise'
    elif overlap / max(len(user_skills), len(other_skills)) > 0.7:
        score = 50
        reason = 'Similar skill sets - limited complementarity'
    else:
        # Good complement
        score = min(70 + (total_unique * 3), 100)
        reason = f'Great complement - {unique_to_other} unique skills they bring'
    
    # Bonus: Check if their skills match what user is looking for
    if user_looking:
        skill_keywords = ['technical', 'developer', 'engineer', 'cto', 'marketing', 'sales', 'business', 'product', 'design']
        for keyword in skill_keywords:
            if keyword in user_looking:
                # Check if other person has relevant skills
                relevant_skills = {
                    'technical': ['React', 'Python', 'JavaScript', 'Node.js', 'AWS', 'Docker', 'TypeScript'],
                    'developer': ['React', 'Python', 'JavaScript', 'Node.js'],
                    'engineer': ['AWS', 'Docker', 'Machine Learning', 'Blockchain'],
                    'marketing': ['Marketing', 'Sales', 'Product Management'],
                    'sales': ['Sales', 'Marketing', 'Operations'],
                    'business': ['Sales', 'Marketing', 'Finance', 'Operations'],
                    'product': ['Product Management', 'UX Design', 'Web Design'],
                    'design': ['Web Design', 'UX Design', 'Mobile Development'],
                }
                if keyword in relevant_skills:
                    if any(skill in other_skills for skill in relevant_skills.get(keyword, [])):
                        score = min(score + 15, 100)
                        reason = f'Perfect match - they have the {keyword} skills you need!'
                        break
    
    return {'score': score, 'reason': reason}

def calculate_vision_score(user_answers, other_answers):
    """Calculate vision alignment - same goals and approach?"""
    if not user_answers or not other_answers:
        return {'score': 50, 'reason': 'Compatibility quiz not completed'}
    
    alignment_count = 0
    total_questions = 0
    differences = []
    
    vision_questions = ['exit_strategy', 'funding', 'timeline']
    for q in vision_questions:
        user_val = user_answers.get(q)
        other_val = other_answers.get(q)
        if user_val and other_val:
            total_questions += 1
            if user_val == other_val:
                alignment_count += 1
            else:
                differences.append(q)
    
    if total_questions == 0:
        return {'score': 50, 'reason': 'Vision questions not answered'}
    
    alignment_pct = alignment_count / total_questions
    score = alignment_pct * 100
    
    if score >= 90:
        reason = 'Perfect vision alignment - same exit goals and funding approach!'
    elif score >= 70:
        reason = f'Good alignment - {alignment_count}/{total_questions} key goals match'
    elif score >= 50:
        reason = f'Some differences - discuss {", ".join(differences)}'
    else:
        reason = 'Significant vision differences - may need compromise'
    
    return {'score': round(score), 'reason': reason}

def calculate_work_style_score(user_answers, other_answers):
    """Calculate work style compatibility"""
    if not user_answers or not other_answers:
        return {'score': 50, 'reason': 'Work style quiz not completed'}
    
    compatible_pairs = {
        'decision_making': {
            'consensus': ['consensus', 'data_driven'],
            'move_fast': ['move_fast', 'data_driven'],
            'data_driven': ['consensus', 'move_fast', 'data_driven'],
        },
        'work_hours': {
            'structured': ['structured', 'flexible'],
            'flexible': ['flexible', 'intense'],
            'intense': ['intense', 'flexible'],
        },
        'communication': {
            'async': ['async', 'meetings'],
            'realtime': ['realtime', 'meetings'],
            'meetings': ['async', 'realtime', 'meetings'],
        },
    }
    
    compatible_count = 0
    total_questions = 0
    
    for category, pairs in compatible_pairs.items():
        user_val = user_answers.get(category)
        other_val = other_answers.get(category)
        if user_val and other_val:
            total_questions += 1
            if other_val in pairs.get(user_val, []):
                compatible_count += 1
    
    if total_questions == 0:
        return {'score': 50, 'reason': 'Work style questions not answered'}
    
    score = (compatible_count / total_questions) * 100
    
    if score >= 90:
        reason = 'Excellent work style match - you will collaborate smoothly'
    elif score >= 70:
        reason = 'Good compatibility - minor adjustments needed'
    else:
        reason = 'Different work styles - requires open communication'
    
    return {'score': round(score), 'reason': reason}

def calculate_stage_score(user, other):
    """Calculate project stage alignment"""
    user_projects = user.get('projects') or []
    other_projects = other.get('projects') or []
    
    if not user_projects or not other_projects:
        return {'score': 70, 'reason': 'No project stage information'}
    
    user_stage = user_projects[0].get('stage') if user_projects else None
    other_stage = other_projects[0].get('stage') if other_projects else None
    
    if not user_stage or not other_stage:
        return {'score': 70, 'reason': 'Stage information incomplete'}
    
    stage_compatibility = {
        'idea': ['idea', 'mvp'],
        'mvp': ['idea', 'mvp', 'early-stage'],
        'early-stage': ['mvp', 'early-stage', 'growth'],
        'growth': ['early-stage', 'growth'],
    }
    
    if other_stage in stage_compatibility.get(user_stage, []):
        score = 95
        reason = f'Perfect timing - both at {user_stage} stage'
    elif user_stage == other_stage:
        score = 100
        reason = f'Identical stage - both at {user_stage}'
    else:
        score = 60
        reason = f'Different stages - may have different priorities'
    
    return {'score': score, 'reason': reason}

def calculate_location_score(user, other):
    """Calculate location compatibility"""
    user_location = (user.get('location') or '').lower()
    other_location = (other.get('location') or '').lower()
    
    if not user_location or not other_location:
        return {'score': 70, 'reason': 'Location not specified'}
    
    # Check for exact match
    if user_location == other_location:
        return {'score': 100, 'reason': 'Same location - can meet in person!'}
    
    # Check for remote
    if 'remote' in user_location or 'remote' in other_location:
        return {'score': 90, 'reason': 'Remote-friendly - location flexible'}
    
    # Check for same city
    user_city = user_location.split(',')[0].strip()
    other_city = other_location.split(',')[0].strip()
    if user_city == other_city:
        return {'score': 95, 'reason': f'Same city ({user_city}) - easy to collaborate'}
    
    # Check for same state/country
    if ',' in user_location and ',' in other_location:
        user_region = user_location.split(',')[-1].strip()
        other_region = other_location.split(',')[-1].strip()
        if user_region == other_region:
            return {'score': 75, 'reason': f'Same region ({user_region}) - occasional meetups possible'}
    
    # Different locations
    return {'score': 50, 'reason': 'Different locations - remote collaboration needed'}

def add_match_scores_to_founders(current_user_profile, founders_list, current_user_project=None):
    """
    Add match scores to list of founders/projects
    
    Args:
        current_user_profile: The current user's profile
        founders_list: List of founder profiles (may contain project info in 'projects' array)
        current_user_project: Optional current user's project object (for project-based matching)
    
    Returns:
        List of founders with match_score and compatibility_breakdown added
    """
    enriched_founders = []
    
    for founder in founders_list:
        # Extract project information if available
        other_project = None
        if founder.get('primary_project_id') and founder.get('projects'):
            # Find the primary project being matched
            primary_project = next(
                (p for p in founder['projects'] if p.get('id') == founder.get('primary_project_id')),
                founder['projects'][0] if founder['projects'] else None
            )
            if primary_project:
                other_project = primary_project
        elif founder.get('projects') and len(founder['projects']) > 0:
            # Use first project if no primary specified
            other_project = founder['projects'][0]
        
        # Calculate match score using project-level compatibility answers
        match_data = calculate_match_score(
            current_user_profile, 
            founder, 
            user_project=current_user_project,
            other_project=other_project
        )
        founder['match_score'] = match_data['overall_score']
        founder['compatibility_breakdown'] = match_data['breakdown']
        enriched_founders.append(founder)
    
    # Sort by match score (highest first)
    enriched_founders.sort(key=lambda x: x.get('match_score', 0), reverse=True)
    
    return enriched_founders

