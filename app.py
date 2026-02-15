"""Flask application with route handlers"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import traceback

from utils.auth import get_clerk_user_id
from utils.validation import sanitize_string, validate_integer, sanitize_list, validate_enum
from utils.logger import log_error, log_warning, log_info
from utils.rate_limit import init_rate_limiter, RATE_LIMITS
from config.database import get_supabase
from services import founder_service, project_service, swipe_service, profile_service, match_service, waitlist_service, message_service, payment_service, workspace_service, task_service
from services import plan_service, subscription_service, document_service, feedback_service, advanced_search_service, advisor_service, admin_service, feed_service
from services.notification_service import NotificationService, ApprovalService

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:3000", 
            "http://127.0.0.1:5000", 
            "https://guild-space.co/",
            "https://guild-space.co",
            "https://beta-branch.dc301xqwoyccc.amplifyapp.com",
            "https://beta-branch.dc301xqwoyccc.amplifyapp.com/"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        "allow_headers": ["Content-Type", "X-Clerk-User-Id", "X-User-Email", "X-User-Name"],
        "supports_credentials": True
    }
})

# Initialize rate limiter
limiter = init_rate_limiter(app)

# Shared helper to get founder_id from clerk_user_id (reduces duplicate code)
def _get_founder_id_from_clerk(clerk_user_id):
    """Get founder ID from clerk_user_id. Returns (founder_id, error_response) tuple.
    If successful, error_response is None. If failed, founder_id is None.
    """
    supabase = get_supabase()
    founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
    if not founder.data:
        return None, (jsonify({"error": "Founder not found"}), 404)
    return founder.data[0]['id'], None

@app.route('/')
def home():
    return jsonify({
        "message": "Founders Matching API",
        "status": "running",
        "version": "1.0.0"
    })

@app.route('/health')
@limiter.exempt
def health_check():
    return jsonify({
        "status": "healthy",
        "message": "API is running successfully"
    })

@app.route('/api/founders', methods=['GET'])
def get_founders():
    """Get founders for swiping (excludes current user and already swiped)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required", "received_headers": [k for k, v in request.headers]}), 401
        
        # Get and validate filter parameters from query string
        filters = {
            'skills': sanitize_list(request.args.getlist('skills'), max_items=20),  # Max 20 skills
            'location': sanitize_string(request.args.get('location', ''), max_length=200),
            'project_stage': validate_enum(request.args.get('project_stage', ''), 
                                         ['idea', 'mvp', 'early_revenue', 'scaling', '']),
            'looking_for': sanitize_string(request.args.get('looking_for', ''), max_length=100),
            'search': sanitize_string(request.args.get('search', ''), max_length=200),
            'genre': sanitize_string(request.args.get('genre', ''), max_length=50),
            'limit': validate_integer(request.args.get('limit', 20), min_value=1, max_value=100),
            'offset': validate_integer(request.args.get('offset', 0), min_value=0),
            'preferences': sanitize_string(request.args.get('preferences', ''), max_length=5000)
        }
        
        # Validate mode parameter
        mode = validate_enum(request.args.get('mode', 'projects'), ['projects', 'founders'], case_sensitive=False) or 'projects'
        
        founders = founder_service.get_available_founders(clerk_user_id, filters, mode=mode)
        return jsonify(founders)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/founders/onboarding', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def save_onboarding():
    """Save or update onboarding data for a founder"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Validate and sanitize input
        try:
            validated_data = {
                'purpose': sanitize_string(data.get('purpose'), max_length=2000) if data.get('purpose') else None,
                'location': sanitize_string(data.get('location'), max_length=200) if data.get('location') else '',
                'skills': sanitize_list(data.get('skills', []), max_items=50) if data.get('skills') else [],
            }
        except Exception as e:
            return jsonify({"error": f"Invalid input: {str(e)}"}), 400
        
        supabase = get_supabase()
        if not supabase:
            return jsonify({"error": "Database connection not available"}), 500
        
        # Check if founder exists by clerk_user_id
        existing = supabase.table('founders').select('id, email').eq('clerk_user_id', clerk_user_id).execute()
        
        # If not found by clerk_user_id, check by email (case-insensitive) using database query
        if not existing.data and data.get('email'):
            email = data.get('email', '').strip()
            if email:
                # Use ilike for case-insensitive email match instead of loading all founders
                email_match = supabase.table('founders').select('id, email, clerk_user_id').ilike(
                    'email', email
                ).limit(1).execute()
                if email_match.data:
                    founder = email_match.data[0]
                    # Found existing founder with same email - update clerk_user_id
                    supabase.table('founders').update({'clerk_user_id': clerk_user_id}).eq('id', founder['id']).execute()
                    # Reuse the found founder data instead of re-querying
                    existing = {'data': [{'id': founder['id'], 'email': founder.get('email')}]}
        
        if existing.data:
            # Update existing founder
            founder_id = existing.data[0]['id']
            update_data = {
                'purpose': validated_data['purpose'],
                'location': validated_data['location'],
                'skills': validated_data['skills'],
                'onboarding_completed': True
            }
            
            # Also update name and email if provided
            if data.get('name'):
                update_data['name'] = sanitize_string(data.get('name'), max_length=200)
            if data.get('email'):
                email = data.get('email', '').strip()
                if email and '@' in email:  # Basic email validation
                    update_data['email'] = email
            
            # Only update fields that are provided and not None
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            result = supabase.table('founders').update(update_data).eq('id', founder_id).execute()
            
            if not result.data:
                return jsonify({"error": "Failed to update founder"}), 500
            
            # Add projects if provided (only add new ones, skip if already exists)
            if data.get('projects'):
                # Get existing projects to determine display_order
                existing_projects = supabase.table('projects').select('display_order').eq('founder_id', founder_id).execute()
                max_order = max([p['display_order'] for p in existing_projects.data], default=-1) if existing_projects.data else -1
                
                # Batch collect valid projects
                project_rows = []
                for idx, project in enumerate(data['projects'][:10]):  # Limit to 10 projects
                    title = sanitize_string(project.get('title'), max_length=200)
                    description = sanitize_string(project.get('description'), max_length=5000)
                    stage = validate_enum(project.get('stage', 'idea'), 
                                         ['idea', 'mvp', 'early_revenue', 'scaling'], 
                                         case_sensitive=False) or 'idea'
                    
                    if title and description:
                        project_rows.append({
                            'founder_id': founder_id,
                            'title': title,
                            'description': description,
                            'stage': stage,
                            'display_order': max_order + idx + 1
                        })
                
                # Batch insert all projects at once
                if project_rows:
                    try:
                        supabase.table('projects').insert(project_rows).execute()
                    except Exception as project_error:
                        # Log but don't fail if project insert fails (might be duplicate)
                        log_warning(f"Failed to insert projects: {str(project_error)}")
            
            return jsonify(result.data[0]), 200
        else:
            # Create new founder with onboarding data
            if not data.get('purpose'):
                return jsonify({"error": "purpose is required"}), 400
            
            # Generate default looking_for based on purpose if not provided
            purpose = data.get('purpose')
            default_looking_for = ""
            if purpose == 'idea_needs_cofounder':
                default_looking_for = "Looking for a co-founder to help build my idea"
            elif purpose == 'skills_want_project':
                default_looking_for = "Looking to join an exciting project where I can apply my skills"
            elif purpose == 'both':
                default_looking_for = "Open to starting something new or joining an existing project"
            else:
                default_looking_for = "Looking for the right opportunity to build something great"
            
            founder_data = {
                'clerk_user_id': clerk_user_id,
                'name': data.get('name', ''),
                'email': data.get('email', ''),
                'purpose': purpose,
                'location': data.get('location', ''),
                'looking_for': data.get('looking_for', default_looking_for),
                'skills': data.get('skills', []),
                'onboarding_completed': True
            }
            
            result = supabase.table('founders').insert(founder_data).execute()
            
            if not result.data:
                return jsonify({"error": "Failed to create founder"}), 500
            
            founder_id = result.data[0]['id']
            
            # Add projects if provided - batch insert for efficiency
            if data.get('projects'):
                project_rows = []
                for idx, project in enumerate(data['projects'][:10]):  # Limit to 10 projects
                    if project.get('title') and project.get('description'):
                        project_rows.append({
                            'founder_id': founder_id,
                            'title': project['title'],
                            'description': project['description'],
                            'stage': project.get('stage', 'idea'),
                            'display_order': idx
                        })
                
                if project_rows:
                    try:
                        supabase.table('projects').insert(project_rows).execute()
                    except Exception as project_error:
                        # Log but don't fail if project insert fails
                        log_warning(f"Failed to insert projects: {str(project_error)}")
            
            return jsonify(result.data[0]), 201
            
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in save_onboarding", error=e, traceback_str=error_trace)
        return jsonify({
            "error": str(e),
            "traceback": error_trace if app.debug else None
        }), 500

@app.route('/api/founders/onboarding-status', methods=['GET'])
def check_onboarding_status():
    """Check if user has completed onboarding"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        supabase = get_supabase()
        result = supabase.table('founders').select('id, onboarding_completed, purpose, skills').eq('clerk_user_id', clerk_user_id).execute()
        
        if result.data:
            founder = result.data[0]
            return jsonify({
                'exists': True,
                'onboarding_completed': founder.get('onboarding_completed', False),
                'has_purpose': bool(founder.get('purpose')),
                'has_skills': bool(founder.get('skills') and len(founder.get('skills', [])) > 0)
            }), 200
        else:
            return jsonify({
                'exists': False,
                'onboarding_completed': False,
                'has_purpose': False,
                'has_skills': False
            }), 200
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/founders/swipe-limit', methods=['GET'])
def get_swipe_limit():
    """Get swipe limit information for discovery"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        can_swipe, current_count, max_allowed = plan_service.check_discovery_limit(clerk_user_id)
        
        return jsonify({
            "can_swipe": can_swipe,
            "current_count": current_count,
            "max_allowed": max_allowed,
            "remaining": max_allowed - current_count if max_allowed != -1 else -1
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting swipe limit", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/founders', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def create_founder():
    """Create a new founder profile with projects"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        founder = founder_service.create_founder(data)
        return jsonify(founder), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error_trace = traceback.format_exc()
        error_msg = str(e)
        log_error(f"Error in create_founder: {error_msg}", traceback_str=error_trace)
        
        error_response = {"error": error_msg}
        if app.debug:
            error_response["traceback"] = error_trace
        
        return jsonify(error_response), 500

@app.route('/api/projects', methods=['GET'])
def get_projects():
    """Get projects - user's own projects by default, or discoverable projects if discover=true"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Check if this is a discovery request
        discover = request.args.get('discover', '').lower() == 'true'
        
        if discover:
            # Return discoverable projects (other users' projects for swiping)
            filters = {
                'skills': sanitize_list(request.args.getlist('skills'), max_items=20),
                'location': sanitize_string(request.args.get('location', ''), max_length=200),
                'project_stage': validate_enum(request.args.get('project_stage', ''), 
                                             ['idea', 'mvp', 'early_revenue', 'scaling', '']),
                'looking_for': sanitize_string(request.args.get('looking_for', ''), max_length=100),
                'search': sanitize_string(request.args.get('search', ''), max_length=200),
                'genre': sanitize_string(request.args.get('genre', ''), max_length=50),
                'limit': validate_integer(request.args.get('limit', 20), min_value=1, max_value=100),
                'offset': validate_integer(request.args.get('offset', 0), min_value=0),
                'preferences': sanitize_string(request.args.get('preferences', ''), max_length=5000)
            }
            
            # Use project mode for discovery
            projects = founder_service.get_available_founders(clerk_user_id, filters, mode='projects')
            return jsonify(projects)
        else:
            # Return user's own projects (default behavior)
            projects = project_service.get_user_projects(clerk_user_id)
            return jsonify(projects), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in get_projects", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/advanced-search', methods=['GET'])
@limiter.limit(RATE_LIMITS['moderate'])
def advanced_search():
    """Advanced search for Pro+ users - search projects by keyword, genre, stage, region"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Check Pro+ access
        if not advanced_search_service.check_pro_plus_access(clerk_user_id):
            return jsonify({
                "error": "Advanced search is available on Pro+ only.",
                "upgrade_required": True
            }), 403
        
        # Get current user's founder ID
        supabase = get_supabase()
        user_profile = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not user_profile.data:
            return jsonify({"error": "Profile not found. Please create your profile first."}), 404
        
        current_user_id = user_profile.data[0]['id']
        
        # Parse and validate query parameters
        query_params = {
            'q': sanitize_string(request.args.get('q', ''), max_length=200) if request.args.get('q') else None,
            'genre': sanitize_list(request.args.getlist('genre'), max_items=10),
            'stage': sanitize_list(request.args.getlist('stage'), max_items=10),
            'region': sanitize_string(request.args.get('region', ''), max_length=100) if request.args.get('region') else None,
            'timezone_offset_range': sanitize_string(request.args.get('timezone_offset_range', ''), max_length=20) if request.args.get('timezone_offset_range') else None,
            'limit': validate_integer(request.args.get('limit', 50), min_value=1, max_value=200),
            'offset': validate_integer(request.args.get('offset', 0), min_value=0)
        }
        
        # Validate stage values
        valid_stages = ['idea', 'mvp', 'early_revenue', 'scaling', 'revenue', 'other']
        if query_params['stage']:
            query_params['stage'] = [s for s in query_params['stage'] if s in valid_stages]
        
        # Perform search
        results = advanced_search_service.search_projects(query_params, current_user_id)
        
        # Results now returns a dict with 'projects' and 'total'
        return jsonify(results), 200
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in advanced_search", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/projects', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def create_project():
    """Create a new project for a founder - free for all plans"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        project = project_service.create_project(clerk_user_id, data)
        return jsonify(project), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in create_project", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/projects/<project_id>', methods=['PUT'])
def update_project(project_id):
    """Update a project"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        project = project_service.update_project(clerk_user_id, project_id, data)
        return jsonify(project), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/projects/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    """Delete a project"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = project_service.delete_project(clerk_user_id, project_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/swipes', methods=['POST'])
def create_swipe():
    """Record a swipe action - uses plan-based discovery limits"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        swipe = swipe_service.create_swipe(clerk_user_id, data)
        return jsonify(swipe), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in create_swipe", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/profile/check', methods=['GET'])
def check_profile():
    """Check if user has a profile"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = profile_service.check_profile(clerk_user_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Debug endpoint removed for security - do not expose internal state in production

@app.route('/api/matches', methods=['GET'])
@limiter.limit(RATE_LIMITS['standard'])
def get_matches():
    """Get matches for the current user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        matches = match_service.get_matches(clerk_user_id)
        return jsonify(matches)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/matches/<match_id>', methods=['DELETE', 'OPTIONS'])
def delete_match(match_id):
    """Remove a match (unmatch)"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = match_service.unmatch(clerk_user_id, match_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/likes', methods=['GET'])
def get_likes():
    """Get people who liked you (swiped right on you) but you haven't swiped on them yet"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        likes = match_service.get_likes(clerk_user_id)
        return jsonify(likes)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in get_likes", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/counts', methods=['GET'])
def get_notification_counts():
    """Get notification counts for different tabs (interests, workspaces, etc.)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Count interests (unresponded likes)
        interests_count = 0
        try:
            likes = match_service.get_likes(clerk_user_id)
            interests_count = len(likes) if likes else 0
        except Exception:
            # If there's an error, just return 0
            interests_count = 0
        
        # Count workspaces (all workspaces the user is part of)
        workspaces_count = 0
        try:
            workspaces = workspace_service.list_user_workspaces(clerk_user_id)
            workspaces_count = len(workspaces) if workspaces else 0
        except Exception:
            # If there's an error, just return 0
            workspaces_count = 0
        
        return jsonify({
            "interests": interests_count,
            "workspaces": workspaces_count
        }), 200
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in get_notification_counts", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/likes/<swipe_id>/respond', methods=['POST'])
def respond_to_like(swipe_id):
    """Respond to a like - accept (creates match) or reject"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        response_type = data.get('response')
        
        if not response_type:
            return jsonify({"error": "response is required"}), 400
        
        result = match_service.respond_to_like(clerk_user_id, swipe_id, response_type)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in respond_to_like", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/waitlist', methods=['POST'])
def join_waitlist():
    """Add email to waitlist"""
    try:
        data = request.get_json()
        email = data.get('email', '')
        
        result = waitlist_service.join_waitlist(email)
        status_code = 201 if not result.get('already_exists') else 200
        return jsonify(result), status_code
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error_msg = str(e)
        # Handle unique constraint violation
        if 'duplicate' in error_msg.lower() or 'unique' in error_msg.lower():
            return jsonify({"message": "You're already on the waitlist!", "already_exists": True}), 200
        return jsonify({"error": error_msg}), 500

@app.route('/api/matches/<match_id>/messages', methods=['GET'])
def get_messages(match_id):
    """Get all messages for a specific match"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        messages = message_service.get_messages(clerk_user_id, match_id)
        return jsonify(messages)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/matches/<match_id>/messages', methods=['POST'])
def send_message(match_id):
    """Send a message in a match"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        content = data.get('content', '')
        
        if not content:
            return jsonify({"error": "Message content is required"}), 400
        
        message = message_service.send_message(clerk_user_id, match_id, content)
        return jsonify(message), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/matches/<match_id>/messages/read', methods=['POST'])
def mark_messages_read(match_id):
    """Mark messages as read"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = message_service.mark_messages_as_read(clerk_user_id, match_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/messages/unread-count', methods=['GET'])
def get_unread_count():
    """Get total unread message count"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = message_service.get_unread_count(clerk_user_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/payments/webhook', methods=['POST'])
@limiter.limit(RATE_LIMITS['strict'])
def handle_webhook():
    """Handle Polar webhook events (legacy - kept for backward compatibility)"""
    try:
        # Get raw body for signature verification (must be done before parsing JSON)
        payload = request.get_data(as_text=True)
        signature = request.headers.get('X-Polar-Webhook-Signature', '')
        
        # Parse JSON payload
        webhook_data = request.get_json()
        
        if not webhook_data:
            return jsonify({"error": "Invalid webhook payload"}), 400
        
        # Verify webhook signature (required for security)
        if not signature:
            return jsonify({"error": "Missing webhook signature"}), 401
        
        if not payment_service.verify_webhook_signature(payload, signature):
            return jsonify({"error": "Invalid webhook signature"}), 401
        
        # Handle webhook event
        result = payment_service.handle_webhook(webhook_data)
        
        return jsonify(result), 200
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error handling webhook", traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/webhook', methods=['POST'])
@limiter.limit(RATE_LIMITS['strict'])
def handle_subscription_webhook():
    """Handle Polar webhook events for subscriptions using Standard Webhooks"""
    import json as json_module
    
    try:
        # Get raw body for signature verification (must be done before parsing JSON)
        body = request.get_data()
        headers = dict(request.headers)
        
        # Log incoming webhook for debugging
        log_info(f"Received billing webhook, content-length: {len(body)}")
        
        # Always log the raw payload for debugging (truncated)
        try:
            raw_payload = json_module.loads(body)
            event_type_raw = raw_payload.get('type', 'unknown')
            log_info(f"Raw webhook event: {event_type_raw}")
            
            # Log key structure for debugging
            data_obj = raw_payload.get('data', {})
            log_info(f"Webhook data structure: {list(data_obj.keys())[:20]}")
            
            # Log customer structure if present
            if data_obj.get('customer'):
                customer_obj = data_obj.get('customer', {})
                log_info(f"Customer structure: {list(customer_obj.keys())[:15]}")
                if customer_obj.get('metadata'):
                    log_info(f"Customer metadata found: {customer_obj.get('metadata')}")
                if customer_obj.get('email'):
                    log_info(f"Customer email: {customer_obj.get('email')}")
        except Exception as parse_err:
            log_info(f"Could not parse raw payload for debugging: {parse_err}")
        
        # Validate webhook using Polar SDK (Standard Webhooks specification)
        webhook_data = subscription_service.validate_webhook_event(body, headers)
        
        if webhook_data is None:
            log_error("Webhook validation failed - attempting fallback")
            # Fallback: try parsing JSON directly (for debugging only)
            try:
                webhook_data = json_module.loads(body)
                log_info("Using fallback JSON parsing (signature not verified)")
            except:
                log_error("Fallback JSON parsing also failed")
                return jsonify({"error": "Invalid webhook signature"}), 401
        
        event_type = webhook_data.get('type', 'unknown')
        log_info(f"Webhook event type: {event_type}")
        
        # Handle subscription webhook event
        result = subscription_service.handle_subscription_webhook(webhook_data)
        
        log_info(f"Webhook processing result: {result}")
        
        return jsonify(result), 200
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error handling subscription webhook", traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/payments/history', methods=['GET'])
def get_payment_history():
    """Get payment history for current user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        payments = payment_service.get_payment_history(clerk_user_id)
        return jsonify(payments), 200
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error fetching payment history", traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

# ==================== Workspace API Routes ====================

@app.route('/api/matches/<match_id>/workspace', methods=['GET'])
def get_workspace_by_match(match_id):
    """Get workspace ID for a match"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        supabase = get_supabase()
        workspace = supabase.table('workspaces').select('id').eq('match_id', match_id).execute()
        
        if not workspace.data:
            # Try to create workspace if it doesn't exist
            try:
                workspace_id = workspace_service.create_workspace_for_match(match_id)
                workspace_data = workspace_service.get_workspace(clerk_user_id, workspace_id)
                return jsonify(workspace_data), 200
            except Exception as e:
                log_error(f"Error creating workspace for match {match_id}", error=e)
                import traceback
                traceback.print_exc()
                return jsonify({"error": f"Workspace not found and failed to create: {str(e)}"}), 500
        
        workspace_id = workspace.data[0]['id']
        workspace_data = workspace_service.get_workspace(clerk_user_id, workspace_id)
        return jsonify(workspace_data), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces', methods=['GET'])
@limiter.limit(RATE_LIMITS['standard'])
def list_workspaces():
    """Get all workspaces for the current user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        workspaces = workspace_service.list_user_workspaces(clerk_user_id)
        return jsonify(workspaces), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>', methods=['GET'])
@limiter.limit(RATE_LIMITS['standard'])
def get_workspace(workspace_id):
    """Get workspace overview"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        workspace = workspace_service.get_workspace(clerk_user_id, workspace_id)
        return jsonify(workspace), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>', methods=['PATCH'])
def update_workspace(workspace_id):
    """Update workspace title and stage"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        workspace = workspace_service.update_workspace(clerk_user_id, workspace_id, data)
        return jsonify(workspace), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/context', methods=['GET'])
@limiter.limit(RATE_LIMITS['standard'])
def get_workspace_context(workspace_id):
    """Get combined workspace context data in a single API call.
    
    This endpoint consolidates multiple data fetches (participants, KPIs, decisions,
    roles, checkins, equity) to reduce the number of API calls when loading workspace tabs.
    """
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        context = workspace_service.get_workspace_context(clerk_user_id, workspace_id)
        return jsonify(context), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/participants', methods=['GET'])
def get_workspace_participants(workspace_id):
    """Get workspace participants"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        participants = workspace_service.get_participants(clerk_user_id, workspace_id)
        return jsonify(participants), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/participants/<user_id>', methods=['PATCH'])
def update_workspace_participant(workspace_id, user_id):
    """Update participant details"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        participant = workspace_service.update_participant(clerk_user_id, workspace_id, user_id, data)
        return jsonify(participant), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/decisions', methods=['GET'])
def get_workspace_decisions(workspace_id):
    """Get workspace decisions (paginated)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        tag = request.args.get('tag')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        
        decisions = workspace_service.get_decisions(clerk_user_id, workspace_id, tag, page, limit)
        return jsonify(decisions), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/decisions', methods=['POST'])
def create_workspace_decision(workspace_id):
    """Create a new decision"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid data format"}), 400
        
        # Basic validation
        if 'content' in data:
            if not isinstance(data['content'], str):
                return jsonify({"error": "content must be a string"}), 400
            if len(data['content']) > 10000:  # Reasonable limit
                return jsonify({"error": "content too long (max 10000 characters)"}), 400
        
        decision = workspace_service.create_decision(clerk_user_id, workspace_id, data)
        return jsonify(decision), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/decisions/<decision_id>', methods=['PATCH'])
def update_workspace_decision(decision_id):
    """Update a decision (5 minute edit window)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid data format"}), 400
        
        # Basic validation
        if 'content' in data and data['content']:
            if not isinstance(data['content'], str):
                return jsonify({"error": "content must be a string"}), 400
            if len(data['content']) > 10000:  # Reasonable limit
                return jsonify({"error": "content too long (max 10000 characters)"}), 400
        
        decision = workspace_service.update_decision(clerk_user_id, decision_id, data)
        return jsonify(decision), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/equity', methods=['GET'])
def get_workspace_equity(workspace_id):
    """Get equity scenarios"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        equity = workspace_service.get_equity_scenarios(clerk_user_id, workspace_id)
        return jsonify(equity), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/equity-scenarios', methods=['POST'])
def create_equity_scenario(workspace_id):
    """Create a new equity scenario"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        scenario = workspace_service.create_equity_scenario(clerk_user_id, workspace_id, data)
        return jsonify(scenario), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/equity-scenarios/<scenario_id>/set-current', methods=['POST'])
def set_current_equity_scenario(workspace_id, scenario_id):
    """Set an equity scenario as current"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        scenario = workspace_service.set_current_equity_scenario(clerk_user_id, scenario_id)
        
        # If the scenario has a message (already pending or already current), return 200 with message
        if 'message' in scenario:
            return jsonify(scenario), 200
        
        return jsonify(scenario), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/equity-scenarios/<scenario_id>', methods=['PATCH'])
def update_equity_scenario(scenario_id):
    """Update an equity scenario (currently only note)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        note = data.get('note', '')
        
        scenario = workspace_service.update_equity_scenario_note(clerk_user_id, scenario_id, note)
        return jsonify(scenario), 200
    except ValueError as e:
        log_error("ValueError updating scenario note", error=e)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error updating scenario note", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/agreement-draft', methods=['GET'])
def get_agreement_draft(workspace_id):
    """Generate a founders' agreement draft"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        draft = workspace_service.generate_agreement_draft(clerk_user_id, workspace_id)
        return jsonify(draft), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/roles', methods=['GET'])
def get_workspace_roles(workspace_id):
    """Get workspace roles"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        roles = workspace_service.get_roles(clerk_user_id, workspace_id)
        return jsonify(roles), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/roles/<user_id>', methods=['PUT'])
def upsert_workspace_role(workspace_id, user_id):
    """Upsert role for a user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        role = workspace_service.upsert_role(clerk_user_id, workspace_id, user_id, data)
        return jsonify(role), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/kpis', methods=['GET'])
def get_workspace_kpis(workspace_id):
    """Get workspace KPIs"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        kpis = workspace_service.get_kpis(clerk_user_id, workspace_id)
        return jsonify(kpis), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/kpis', methods=['POST'])
def create_workspace_kpi(workspace_id):
    """Create a new KPI"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        kpi = workspace_service.create_kpi(clerk_user_id, workspace_id, data)
        return jsonify(kpi), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/kpis/<kpi_id>', methods=['PATCH'])
def update_workspace_kpi(kpi_id):
    """Update a KPI"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        kpi = workspace_service.update_kpi(clerk_user_id, kpi_id, data)
        return jsonify(kpi), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins', methods=['GET'])
def get_workspace_checkins(workspace_id):
    """Get workspace checkins"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        limit = int(request.args.get('limit', 3))
        checkins = workspace_service.get_checkins(clerk_user_id, workspace_id, limit)
        return jsonify(checkins), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins', methods=['POST'])
def create_workspace_checkin(workspace_id):
    """Create a new checkin"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        checkin = workspace_service.create_checkin(clerk_user_id, workspace_id, data)
        return jsonify(checkin), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =================== NOTIFICATION ENDPOINTS ===================
notification_service = NotificationService()
approval_service = ApprovalService()

@app.route('/api/notifications/summary', methods=['GET'])
def get_notifications_summary():
    """Get notification summary for multiple workspaces"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        workspace_ids = request.args.getlist('workspace_ids[]')
        if not workspace_ids:
            return jsonify({"error": "workspace_ids required"}), 400
        
        # Get founder ID using shared helper
        founder_id, error_response = _get_founder_id_from_clerk(clerk_user_id)
        if error_response:
            return error_response
        
        supabase = get_supabase()
        
        # Batch query: Get all pending approvals for requested workspaces in one query
        all_approvals = supabase.table('approvals').select('workspace_id').eq(
            'approver_user_id', founder_id
        ).eq('status', 'PENDING').in_('workspace_id', workspace_ids).execute()
        
        # Batch query: Get all unread notifications for requested workspaces in one query
        # A notification is considered "unread" when read_at IS NULL
        all_notifications = supabase.table('notifications').select('workspace_id').eq(
            'user_id', founder_id
        ).in_('workspace_id', workspace_ids).is_('read_at', 'null').execute()
        
        # Aggregate counts by workspace_id in Python
        approval_counts = {}
        for approval in (all_approvals.data or []):
            ws_id = approval['workspace_id']
            approval_counts[ws_id] = approval_counts.get(ws_id, 0) + 1
        
        notification_counts = {}
        for notification in (all_notifications.data or []):
            ws_id = notification['workspace_id']
            notification_counts[ws_id] = notification_counts.get(ws_id, 0) + 1
        
        # Build summaries for each requested workspace
        summaries = {}
        for workspace_id in workspace_ids:
            summaries[workspace_id] = {
                'pending_approvals': approval_counts.get(workspace_id, 0),
                'unread_updates': notification_counts.get(workspace_id, 0)
                # Note: unread_updates will be 0 when all notifications have read_at set
                # The badge will automatically disappear when this count reaches 0
            }
        
        return jsonify(summaries)
        
    except Exception as e:
        log_error("Error getting notification summaries", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    """Get notifications for current user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        workspace_id = request.args.get('workspace_id')
        if not workspace_id:
            return jsonify({"error": "workspace_id required"}), 400
        
        # Get founder ID using shared helper
        founder_id, error_response = _get_founder_id_from_clerk(clerk_user_id)
        if error_response:
            return error_response
        
        supabase = get_supabase()
        
        # Get notifications
        query = supabase.table('notifications').select(
            '*, actor:founders!notifications_actor_user_id_fkey(name)'
        ).eq('user_id', founder_id).eq('workspace_id', workspace_id)
        
        # Filter by read status if requested
        if request.args.get('unread') == 'true':
            query = query.is_('read_at', 'null')
        
        # Order and limit
        notifications = query.order('created_at', desc=True).limit(50).execute()
        
        return jsonify(notifications.data or [])
        
    except Exception as e:
        log_error("Error getting notifications", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/<notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):
    """Mark a notification as read"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Get founder ID using shared helper
        founder_id, error_response = _get_founder_id_from_clerk(clerk_user_id)
        if error_response:
            return error_response
        
        supabase = get_supabase()
        
        # Update notification
        from datetime import datetime
        result = supabase.table('notifications').update({
            'read_at': datetime.now().isoformat()
        }).eq('id', notification_id).eq('user_id', founder_id).execute()
        
        if not result.data:
            return jsonify({"error": "Notification not found or unauthorized"}), 404
        
        return jsonify({"success": True})
        
    except Exception as e:
        log_error("Error marking notification read", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/mark-all-read', methods=['POST'])
def mark_all_notifications_read():
    """Mark all notifications as read for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        workspace_id = data.get('workspace_id')
        if not workspace_id:
            return jsonify({"error": "workspace_id required"}), 400
        
        # Get founder ID using shared helper
        founder_id, error_response = _get_founder_id_from_clerk(clerk_user_id)
        if error_response:
            return error_response
        
        supabase = get_supabase()
        
        # Update all unread notifications
        from datetime import datetime
        result = supabase.table('notifications').update({
            'read_at': datetime.now().isoformat()
        }).eq('user_id', founder_id).eq('workspace_id', workspace_id).is_('read_at', 'null').execute()
        
        return jsonify({
            "success": True,
            "count": len(result.data) if result.data else 0
        })
        
    except Exception as e:
        log_error("Error marking all notifications read", error=e)
        return jsonify({"error": str(e)}), 500

# Debug endpoint removed for security - do not expose internal state in production

@app.route('/api/approvals/pending', methods=['GET'])
def get_pending_approvals():
    """Get pending approvals for current user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        workspace_id = request.args.get('workspace_id')
        if not workspace_id:
            return jsonify({"error": "workspace_id required"}), 400
        
        approvals = approval_service.get_pending_approvals(clerk_user_id, workspace_id)
        return jsonify(approvals)
        
    except Exception as e:
        log_error("Error getting pending approvals", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/approvals/<approval_id>/approve', methods=['POST'])
def approve_request(approval_id):
    """Approve a pending request"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        comment = data.get('comment')
        
        success = approval_service.process_approval(
            clerk_user_id, approval_id, 'approve', comment
        )
        
        return jsonify({"success": success})
        
    except Exception as e:
        log_error("Error approving request", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/approvals/<approval_id>/reject', methods=['POST'])
def reject_request(approval_id):
    """Reject a pending request"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        comment = data.get('comment')
        
        success = approval_service.process_approval(
            clerk_user_id, approval_id, 'reject', comment
        )
        
        return jsonify({"success": success})
        
    except Exception as e:
        log_error("Error rejecting request", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/equity-scenarios/<scenario_id>/propose', methods=['POST'])
def propose_equity_change(workspace_id, scenario_id):
    """Create or update equity scenario as proposal"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        
        # Create approval
        approval_id = approval_service.create_approval(
            clerk_user_id=clerk_user_id,
            workspace_id=workspace_id,
            entity_type='EQUITY_SCENARIO',
            entity_id=scenario_id,
            proposed_data=data
        )
        
        # Update scenario with pending status
        supabase = get_supabase()
        supabase.table('workspace_equity_scenarios').update({
            'approval_status': 'PENDING',
            'approval_id': approval_id
        }).eq('id', scenario_id).execute()
        
        return jsonify({
            "success": True,
            "approval_id": approval_id
        })
        
    except Exception as e:
        log_error("Error proposing equity change", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/notification-preferences', methods=['GET', 'PUT'])
def notification_preferences():
    """Get or update notification preferences"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        workspace_id = request.args.get('workspace_id') or request.get_json().get('workspace_id')
        if not workspace_id:
            return jsonify({"error": "workspace_id required"}), 400
        
        # Get founder ID using shared helper
        founder_id, error_response = _get_founder_id_from_clerk(clerk_user_id)
        if error_response:
            return error_response
        
        supabase = get_supabase()
        
        if request.method == 'GET':
            # Get preferences
            prefs = supabase.table('notification_preferences').select('*').eq(
                'user_id', founder_id
            ).eq('workspace_id', workspace_id).execute()
            
            if prefs.data:
                return jsonify(prefs.data[0])
            else:
                # Return defaults
                return jsonify({
                    'email_enabled': True,
                    'email_digest': False,
                    'in_app_enabled': True,
                    'approval_emails': True
                })
        
        else:  # PUT
            data = request.get_json()
            from datetime import datetime
            
            # Upsert preferences
            pref_data = {
                'user_id': founder_id,
                'workspace_id': workspace_id,
                'email_enabled': data.get('email_enabled', True),
                'email_digest': data.get('email_digest', False),
                'in_app_enabled': data.get('in_app_enabled', True),
                'approval_emails': data.get('approval_emails', True),
                'updated_at': datetime.now().isoformat()
            }
            
            result = supabase.table('notification_preferences').upsert(
                pref_data,
                on_conflict='user_id,workspace_id'
            ).execute()
            
            return jsonify(result.data[0] if result.data else pref_data)
        
    except Exception as e:
        log_error("Error with notification preferences", error=e)
        return jsonify({"error": str(e)}), 500

# Task Board Endpoints
@app.route('/api/workspaces/<workspace_id>/tasks', methods=['GET'])
def get_workspace_tasks(workspace_id):
    """Get all tasks for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        owner_filter = request.args.get('owner')  # 'me', 'other', or None for all
        link_filter = request.args.get('link')  # 'kpi', 'decision', or None for all
        
        tasks = task_service.get_tasks(clerk_user_id, workspace_id, owner_filter, link_filter)
        return jsonify(tasks)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting tasks", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/tasks', methods=['POST'])
def create_workspace_task(workspace_id):
    """Create a new task"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        task = task_service.create_task(clerk_user_id, workspace_id, data)
        return jsonify(task), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating task", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/tasks/<task_id>', methods=['PATCH'])
def update_workspace_task(workspace_id, task_id):
    """Update a task"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        task = task_service.update_task(clerk_user_id, workspace_id, task_id, data)
        return jsonify(task)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error updating task", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/tasks/<task_id>', methods=['DELETE'])
def delete_workspace_task(workspace_id, task_id):
    """Delete a task"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        task_service.delete_task(clerk_user_id, workspace_id, task_id)
        return jsonify({"message": "Task deleted"}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error deleting task", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/task-metrics', methods=['GET'])
def get_task_metrics(workspace_id):
    """Get task metrics for investor reporting"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        from_date = request.args.get('from')
        to_date = request.args.get('to')
        
        metrics = task_service.get_task_metrics(clerk_user_id, workspace_id, from_date, to_date)
        return jsonify(metrics)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting task metrics", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/tasks/completed-for-week', methods=['GET'])
def get_completed_tasks_for_week(workspace_id):
    """Get completed tasks for a specific week (for check-ins)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        week_start = request.args.get('week_start')
        week_end = request.args.get('week_end')
        
        if not week_start or not week_end:
            return jsonify({"error": "week_start and week_end parameters required"}), 400
        
        tasks = task_service.get_completed_tasks_for_week(clerk_user_id, workspace_id, week_start, week_end)
        return jsonify(tasks)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting completed tasks", error=e)
        return jsonify({"error": str(e)}), 500

# ==================== ADVISOR ENDPOINTS ====================

@app.route('/api/advisors/profile', methods=['GET', 'POST', 'PUT'])
def advisor_profile():
    """Get, create, or update advisor profile"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        if request.method == 'GET':
            profile = advisor_service.get_advisor_profile(clerk_user_id)
            if not profile:
                # Return 200 with null to indicate profile doesn't exist (not an error)
                return jsonify(None), 200
            return jsonify(profile), 200
        else:  # POST or PUT
            data = request.get_json()
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            log_info(f"Creating advisor profile for clerk_user_id: {clerk_user_id}")
            log_info(f"Request data: {data}")
            
            # Get user name and email from request if available (for creating minimal founder profile)
            user_name = data.get('user_name') or request.headers.get('X-User-Name')
            user_email = data.get('user_email') or request.headers.get('X-User-Email')
            
            # Don't log PII - log only that user info was provided
            log_info(f"Creating advisor profile - user info provided: name={'yes' if user_name else 'no'}, email={'yes' if user_email else 'no'}")
            
            # Handle contact info updates separately if provided
            contact_info = {}
            if 'contact_email' in data:
                contact_info['contact_email'] = data.pop('contact_email')
            if 'meeting_link' in data:
                contact_info['meeting_link'] = data.pop('meeting_link')
            if 'contact_note' in data:
                contact_info['contact_note'] = data.pop('contact_note')
            
            profile = advisor_service.create_advisor_profile(
                clerk_user_id, 
                data, 
                user_name=user_name, 
                user_email=user_email
            )
            
            # Update contact info if provided
            if contact_info:
                advisor_service.update_advisor_contact_info(clerk_user_id, contact_info)
                # Refresh profile to include contact info
                profile = advisor_service.get_advisor_profile(clerk_user_id)
            
            log_info(f"Advisor profile created successfully: {profile.get('id')}")
            return jsonify(profile), 201 if request.method == 'POST' else 200
    except ValueError as e:
        error_msg = str(e)
        log_error(f"ValueError in advisor_profile: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": error_msg}), 400
    except Exception as e:
        error_msg = str(e)
        error_trace = traceback.format_exc()
        log_error(f"Error with advisor profile: {error_msg}", traceback_str=error_trace)
        return jsonify({
            "error": error_msg,
            "traceback": error_trace if app.debug else None
        }), 500


# LinkedIn OAuth endpoints for advisor verification
from services import linkedin_service

@app.route('/api/advisors/linkedin/status', methods=['GET'])
def get_linkedin_status():
    """Get LinkedIn verification status for current advisor"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        status = linkedin_service.get_advisor_linkedin_status(clerk_user_id)
        return jsonify(status), 200
    except Exception as e:
        log_error("Error getting LinkedIn status", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/advisors/linkedin/connect', methods=['GET'])
def linkedin_connect():
    """Initiate LinkedIn OAuth flow for advisor verification"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        if not linkedin_service.is_linkedin_configured():
            return jsonify({
                "error": "LinkedIn verification is not yet configured. Please contact support."
            }), 503
        
        auth_url, state = linkedin_service.get_linkedin_auth_url(clerk_user_id)
        return jsonify({
            "auth_url": auth_url,
            "state": state
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error initiating LinkedIn OAuth", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/advisors/linkedin/callback', methods=['POST'])
def linkedin_callback():
    """Complete LinkedIn OAuth verification"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        code = data.get('code')
        state = data.get('state')
        
        if not code:
            return jsonify({"error": "Authorization code required"}), 400
        
        # Verify state if provided (optional additional security)
        if state:
            stored_user_id = linkedin_service.verify_oauth_state(state)
            if stored_user_id and stored_user_id != clerk_user_id:
                return jsonify({"error": "Invalid OAuth state"}), 400
        
        result = linkedin_service.verify_advisor_linkedin(clerk_user_id, code)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error completing LinkedIn verification", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/advisors/linkedin/revoke', methods=['POST'])
def linkedin_revoke():
    """Revoke LinkedIn verification"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = linkedin_service.revoke_linkedin_verification(clerk_user_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error revoking LinkedIn verification", error=e)
        return jsonify({"error": str(e)}), 500


# ============================================
# WORKSPACE FEED & COLLABORATION ROUTES
# ============================================

@app.route('/api/workspaces/<workspace_id>/feed', methods=['GET'])
def get_workspace_feed(workspace_id):
    """Get activity feed for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        posts = feed_service.get_feed_posts(clerk_user_id, workspace_id, limit, offset)
        return jsonify(posts), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error fetching feed", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/feed', methods=['POST'])
def create_workspace_feed_post(workspace_id):
    """Create a new feed post"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        content = data.get('content', '')
        post_type = data.get('post_type', 'message')
        
        post = feed_service.create_feed_post(clerk_user_id, workspace_id, content, post_type)
        return jsonify(post), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating feed post", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/feed/<post_id>/replies', methods=['POST'])
def create_feed_reply(workspace_id, post_id):
    """Create a reply to a feed post"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        content = data.get('content', '')
        
        reply = feed_service.create_feed_reply(clerk_user_id, workspace_id, post_id, content)
        return jsonify(reply), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating feed reply", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/feed/<post_id>', methods=['DELETE'])
def delete_feed_post(workspace_id, post_id):
    """Delete a feed post"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        feed_service.delete_feed_post(clerk_user_id, workspace_id, post_id)
        return jsonify({"success": True}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error deleting feed post", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/meetings', methods=['GET'])
def get_workspace_meetings(workspace_id):
    """Get meetings for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        meetings = feed_service.get_meetings(clerk_user_id, workspace_id)
        return jsonify(meetings), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error fetching meetings", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/meetings', methods=['POST'])
def create_workspace_meeting(workspace_id):
    """Log a new meeting"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        meeting = feed_service.create_meeting(clerk_user_id, workspace_id, data)
        return jsonify(meeting), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating meeting", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/engagement-checkins/status', methods=['GET'])
def get_engagement_checkin_status(workspace_id):
    """Check if user needs to complete a monthly engagement check-in"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        status = feed_service.get_checkin_status(clerk_user_id, workspace_id)
        return jsonify(status), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error checking engagement check-in status", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/engagement-checkins', methods=['GET'])
def get_engagement_checkins(workspace_id):
    """Get all engagement check-ins for a workspace (advisor-founder relationship)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        checkins = feed_service.get_checkins(clerk_user_id, workspace_id)
        return jsonify(checkins), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error fetching engagement check-ins", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/engagement-checkins', methods=['POST'])
def create_engagement_checkin(workspace_id):
    """Submit a monthly engagement check-in"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        checkin = feed_service.create_checkin(clerk_user_id, workspace_id, data)
        return jsonify(checkin), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating engagement check-in", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/activity-logs', methods=['GET'])
def get_workspace_activity_logs(workspace_id):
    """Get activity logs for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        logs = feed_service.get_activity_logs(clerk_user_id, workspace_id)
        return jsonify(logs), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error fetching activity logs", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/activity-logs', methods=['POST'])
def create_workspace_activity_log(workspace_id):
    """Log advisor activity/hours"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        log = feed_service.create_activity_log(clerk_user_id, workspace_id, data)
        return jsonify(log), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating activity log", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/activity-summary', methods=['GET'])
def get_workspace_activity_summary(workspace_id):
    """Get summary of advisor activity for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        summary = feed_service.get_activity_summary(clerk_user_id, workspace_id)
        return jsonify(summary), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error fetching activity summary", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/participants-with-roles', methods=['GET'])
def get_participants_with_roles(workspace_id):
    """Get all participants with their roles (for attendee selection, etc.)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        participants = feed_service.get_workspace_participants_with_roles(clerk_user_id, workspace_id)
        return jsonify(participants), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error fetching participants", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/advisors/marketplace', methods=['GET'])
def get_advisor_marketplace(workspace_id):
    """Get available advisors for marketplace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        filters = {
            'domain': request.args.get('domain')
        }
        
        advisors = advisor_service.get_available_advisors(workspace_id, filters, clerk_user_id)
        return jsonify(advisors), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting marketplace advisors", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/advisors/request', methods=['POST'])
def create_advisor_request(workspace_id):
    """Create an advisor request from marketplace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data or not data.get('advisor_user_id'):
            return jsonify({"error": "advisor_user_id is required"}), 400
        
        request_data = advisor_service.create_advisor_request(
            clerk_user_id, workspace_id, data['advisor_user_id']
        )
        return jsonify(request_data), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating advisor request", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/advisors/requests', methods=['GET'])
def get_advisor_requests():
    """Get advisor requests for current user (as advisor)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        status = request.args.get('status')  # PENDING, ACCEPTED, DECLINED
        requests = advisor_service.get_advisor_requests(clerk_user_id, status)
        return jsonify(requests), 200
    except Exception as e:
        log_error("Error getting advisor requests", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/advisors/requests/<request_id>/respond', methods=['POST'])
def respond_to_advisor_request(request_id):
    """Accept or decline an advisor request"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data or not data.get('response'):
            return jsonify({"error": "response is required (accept or decline)"}), 400
        
        response_type = data['response']
        
        # For accepts, verify payment was made (per-project fee)
        if response_type == 'accept':
            payment_verified = data.get('payment_verified', False)
            if payment_verified:
                # Frontend claims payment was made after checkout redirect - verify it
                supabase = get_supabase()
                
                # Check for payment record (with small retry for webhook race condition)
                import time
                payment_found = False
                for attempt in range(3):
                    payment_check = supabase.table('advisor_project_payments').select('id').eq('request_id', request_id).eq('clerk_user_id', clerk_user_id).execute()
                    if payment_check.data:
                        payment_found = True
                        break
                    if attempt < 2:
                        time.sleep(1)  # Wait 1 second before retry
                
                if not payment_found:
                    return jsonify({"error": "Payment verification pending. Please wait a moment and try again."}), 402
            else:
                # No payment_verified flag - this means user is trying to accept without going through payment
                return jsonify({"error": "Payment required to accept this project", "payment_required": True}), 402
        
        result = advisor_service.respond_to_advisor_request(
            clerk_user_id, request_id, response_type
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error responding to advisor request", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/advisors/workspaces', methods=['GET'])
def get_advisor_workspaces():
    """Get active workspaces for current user (as advisor)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        workspaces = advisor_service.get_active_workspaces(clerk_user_id)
        return jsonify(workspaces), 200
    except Exception as e:
        log_error("Error getting advisor workspaces", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/advisors/notifications', methods=['GET'])
def get_advisor_notifications():
    """Get all notifications for advisor across all workspaces"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Get founder ID using shared helper
        founder_id, error_response = _get_founder_id_from_clerk(clerk_user_id)
        if error_response:
            return error_response
        
        supabase = get_supabase()
        
        # Get all workspaces where user is an advisor
        try:
            participants = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', founder_id
            ).eq('role', 'ADVISOR').execute()
        except Exception:
            # Fallback if role column doesn't exist
            participants = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', founder_id
            ).execute()
        
        if not participants.data:
            return jsonify([]), 200
        
        workspace_ids = [p['workspace_id'] for p in participants.data]
        
        # Get notifications for all workspaces
        query = supabase.table('notifications').select(
            '*, actor:founders!notifications_actor_user_id_fkey(name), workspace:workspaces!notifications_workspace_id_fkey(id, title)'
        ).eq('user_id', founder_id).in_('workspace_id', workspace_ids)
        
        # Filter by read status if requested
        if request.args.get('unread') == 'true':
            query = query.is_('read_at', 'null')
        
        # Order and limit
        notifications = query.order('created_at', desc=True).limit(100).execute()
        
        return jsonify(notifications.data or []), 200
        
    except Exception as e:
        log_error("Error getting advisor notifications", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/advisors/<advisor_user_id>', methods=['DELETE'])
def remove_advisor(workspace_id, advisor_user_id):
    """Remove an advisor from workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = advisor_service.remove_advisor_from_workspace(
            clerk_user_id, workspace_id, advisor_user_id
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error removing advisor", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/comment', methods=['POST'])
def add_checkin_comment(workspace_id, checkin_id):
    """Add a comment to a check-in"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data or not data.get('comment'):
            return jsonify({"error": "comment is required"}), 400
        
        comment = workspace_service.add_checkin_comment(clerk_user_id, checkin_id, data['comment'])
        return jsonify(comment), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error adding check-in comment", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/verdict', methods=['POST', 'PUT'])
def set_checkin_verdict(workspace_id, checkin_id):
    """Set verdict for a check-in (advisors only)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data or not data.get('verdict'):
            return jsonify({"error": "verdict is required (on_track, at_risk, or off_track)"}), 400
        
        verdict = workspace_service.set_checkin_verdict(clerk_user_id, checkin_id, data['verdict'])
        return jsonify(verdict), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error setting check-in verdict", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/advisor-review', methods=['GET'])
@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/partner-review', methods=['GET'])  # Backward compatibility
def get_checkin_advisor_review(workspace_id, checkin_id):
    """Get advisor review for a check-in (advisors only)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        review = workspace_service.get_checkin_partner_review(clerk_user_id, workspace_id, checkin_id)
        if review:
            return jsonify(review), 200
        else:
            return jsonify(None), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting check-in advisor review", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/advisor-review', methods=['POST'])
@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/partner-review', methods=['POST'])  # Backward compatibility
def upsert_checkin_advisor_review(workspace_id, checkin_id):
    """Create or update advisor review for a check-in (advisors only)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if 'verdict' not in data:
            return jsonify({"error": "verdict is required"}), 400
        
        verdict = data['verdict']
        comment = data.get('comment', '')
        
        review = workspace_service.upsert_checkin_partner_review(
            clerk_user_id, workspace_id, checkin_id, verdict, comment
        )
        return jsonify(review), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error saving check-in advisor review", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/advisor-reviews', methods=['GET'])
@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/partner-reviews', methods=['GET'])  # Backward compatibility
def get_checkin_advisor_reviews(workspace_id, checkin_id):
    """Get all advisor reviews for a check-in (founders can view)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        reviews = workspace_service.get_checkin_partner_reviews_for_founders(clerk_user_id, workspace_id, checkin_id)
        return jsonify(reviews), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting check-in advisor reviews", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/advisor-impact-scorecard', methods=['GET'])
@app.route('/api/workspaces/<workspace_id>/partner-impact-scorecard', methods=['GET'])  # Backward compatibility
def get_advisor_impact_scorecard(workspace_id):
    """Get advisor impact scorecard for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Support both advisor_user_id and partner_user_id for backward compatibility
        advisor_user_id = request.args.get('advisor_user_id') or request.args.get('partner_user_id')
        scorecard = advisor_service.compute_advisor_impact_scorecard(
            clerk_user_id, workspace_id, advisor_user_id
        )
        return jsonify(scorecard), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error computing advisor impact scorecard", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/quarterly-review', methods=['POST'])
def save_quarterly_review(workspace_id):
    """Save quarterly review from founder"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        advisor_user_id = data.get('advisor_user_id') or data.get('partner_user_id')  # Backward compatibility: support partner_user_id
        quarter = data.get('quarter')
        value_rating = data.get('value_rating')
        continue_next_quarter = data.get('continue_next_quarter')
        
        if not all([advisor_user_id, quarter, value_rating is not None, continue_next_quarter is not None]):
            return jsonify({"error": "advisor_user_id, quarter, value_rating, and continue_next_quarter are required"}), 400
        
        review = advisor_service.save_quarterly_review(
            clerk_user_id, workspace_id, advisor_user_id, quarter, value_rating, continue_next_quarter
        )
        return jsonify(review), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error saving quarterly review", error=e)
        return jsonify({"error": str(e)}), 500

# ==================== WORKSPACE DOCUMENTS ENDPOINTS ====================

@app.route('/api/workspaces/<workspace_id>/documents', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def upload_workspace_document(workspace_id):
    """Upload a document to workspace storage"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        category = request.form.get('category')
        description = request.form.get('description')
        
        # Validate description length
        if description and len(description) > 1000:
            return jsonify({"error": "Description must be 1000 characters or less"}), 400
        
        document = document_service.upload_document(
            clerk_user_id, workspace_id, file, category, description
        )
        return jsonify(document), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error uploading document", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/documents', methods=['GET'])
def list_workspace_documents(workspace_id):
    """List documents for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        category = request.args.get('category')
        search = request.args.get('search')
        
        documents = document_service.list_documents(clerk_user_id, workspace_id, category, search)
        return jsonify(documents), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error listing documents", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/documents/<document_id>/url', methods=['GET'])
def get_document_signed_url(workspace_id, document_id):
    """Generate a signed URL for downloading a document"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = document_service.get_document_signed_url(clerk_user_id, workspace_id, document_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error generating signed URL", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/documents/<document_id>', methods=['DELETE'])
@limiter.limit(RATE_LIMITS['moderate'])
def delete_workspace_document(workspace_id, document_id):
    """Delete a document and its stored file"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = document_service.delete_document(clerk_user_id, workspace_id, document_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error deleting document", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/advisors/profile/contact', methods=['PUT'])
def update_advisor_contact():
    """Update advisor contact info"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        result = advisor_service.update_advisor_contact_info(clerk_user_id, data)
        if result:
            return jsonify(result), 200
        else:
            return jsonify({"error": "Failed to update contact info"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error updating advisor contact info", error=e)
        return jsonify({"error": str(e)}), 500

# ==================== ADMIN ENDPOINTS ====================

@app.route('/api/admin/check', methods=['GET'])
def admin_check():
    """Check if current user is admin (for frontend nav)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"is_admin": False}), 200
        return jsonify({"is_admin": admin_service.is_admin(clerk_user_id)}), 200
    except Exception as e:
        log_error("Error checking admin status", error=e)
        return jsonify({"is_admin": False}), 200

@app.route('/api/admin/advisors/pending', methods=['GET'])
def admin_list_pending_advisors():
    """List pending advisor profiles (admin only)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        if not admin_service.is_admin(clerk_user_id):
            return jsonify({"error": "Admin access required"}), 403

        data = admin_service.list_pending_advisors()
        return jsonify(data), 200
    except Exception as e:
        log_error("Error listing pending advisors", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/advisors/<advisor_id>', methods=['GET'])
def admin_get_advisor(advisor_id):
    """Get full advisor profile for review (admin only)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        if not admin_service.is_admin(clerk_user_id):
            return jsonify({"error": "Admin access required"}), 403

        profile = admin_service.get_advisor_by_id(advisor_id)
        if not profile:
            return jsonify({"error": "Advisor not found"}), 404
        return jsonify(profile), 200
    except Exception as e:
        log_error("Error fetching advisor for admin", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/advisors/<advisor_id>/approve', methods=['PATCH', 'POST'])
def admin_approve_advisor(advisor_id):
    """Approve advisor - set status APPROVED and is_discoverable True (admin only)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        if not admin_service.is_admin(clerk_user_id):
            return jsonify({"error": "Admin access required"}), 403

        profile = admin_service.approve_advisor(advisor_id)
        if not profile:
            return jsonify({"error": "Advisor not found or could not be updated"}), 404
        return jsonify(profile), 200
    except Exception as e:
        log_error("Error approving advisor", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/advisors/<advisor_id>/reject', methods=['PATCH', 'POST'])
def admin_reject_advisor(advisor_id):
    """Reject advisor - set status REJECTED (admin only)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        if not admin_service.is_admin(clerk_user_id):
            return jsonify({"error": "Admin access required"}), 403

        profile = admin_service.reject_advisor(advisor_id)
        if not profile:
            return jsonify({"error": "Advisor not found or could not be updated"}), 404
        return jsonify(profile), 200
    except Exception as e:
        log_error("Error rejecting advisor", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/subscription-debug', methods=['GET'])
def admin_subscription_debug():
    """Admin endpoint to debug subscription issues for a user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        if not admin_service.is_admin(clerk_user_id):
            return jsonify({"error": "Admin access required"}), 403
        
        target_user_id = request.args.get('user_id')
        target_email = request.args.get('email')
        
        if not target_user_id and not target_email:
            return jsonify({"error": "user_id or email parameter required"}), 400
        
        supabase = get_supabase()
        
        # Find the user
        if target_user_id:
            founder = supabase.table('founders').select('*').eq('clerk_user_id', target_user_id).execute()
        else:
            founder = supabase.table('founders').select('*').eq('email', target_email).execute()
        
        if not founder.data:
            return jsonify({"error": "User not found"}), 404
        
        user_data = founder.data[0]
        
        # Get webhook processing logs for this user
        webhook_logs = supabase.table('webhook_processing_log').select('*').order('processed_at', desc=True).limit(20).execute()
        
        # Get subscription checkouts for this user
        checkouts = []
        try:
            checkout_result = supabase.table('subscription_checkouts').select('*').eq('clerk_user_id', user_data.get('clerk_user_id')).order('created_at', desc=True).limit(10).execute()
            checkouts = checkout_result.data or []
        except Exception:
            pass
        
        return jsonify({
            "user": {
                "id": user_data.get('id'),
                "clerk_user_id": user_data.get('clerk_user_id'),
                "email": user_data.get('email'),
                "plan": user_data.get('plan'),
                "subscription_id": user_data.get('subscription_id'),
                "subscription_status": user_data.get('subscription_status'),
                "subscription_current_period_end": user_data.get('subscription_current_period_end'),
            },
            "recent_webhook_logs": webhook_logs.data or [],
            "checkouts": checkouts,
        }), 200
    except Exception as e:
        log_error("Error in subscription debug", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/subscription-fix', methods=['POST'])
def admin_subscription_fix():
    """Admin endpoint to manually fix a user's subscription"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        if not admin_service.is_admin(clerk_user_id):
            return jsonify({"error": "Admin access required"}), 403
        
        data = request.get_json()
        target_user_id = data.get('user_id')
        new_plan = data.get('plan')
        
        if not target_user_id or not new_plan:
            return jsonify({"error": "user_id and plan are required"}), 400
        
        if new_plan not in ['FREE', 'PRO', 'PRO_PLUS']:
            return jsonify({"error": "Invalid plan. Must be FREE, PRO, or PRO_PLUS"}), 400
        
        from datetime import datetime, timezone, timedelta
        
        # Set subscription period (30 days from now for paid plans)
        current_period_end = None
        if new_plan != 'FREE':
            current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
        
        # Update the plan
        updated_plan = plan_service.update_founder_plan(
            target_user_id,
            new_plan,
            subscription_status='active' if new_plan != 'FREE' else None,
            current_period_end=current_period_end
        )
        
        log_info(f"Admin manually updated plan for {target_user_id} to {new_plan}")
        
        return jsonify({
            "success": True,
            "message": f"Plan updated to {new_plan}",
            "updated_plan": updated_plan
        }), 200
    except Exception as e:
        log_error("Error in subscription fix", error=e)
        return jsonify({"error": str(e)}), 500


# ==================== BILLING & PLAN ENDPOINTS ====================

@app.route('/api/billing/plans', methods=['GET'])
def get_plans():
    """Get all available founder plans"""
    try:
        return jsonify({
            'founder_plans': plan_service.FOUNDER_PLANS,
            'advisor_pricing': plan_service.ADVISOR_PRICING,
        }), 200
    except Exception as e:
        log_error("Error getting plans", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/my-plan', methods=['GET'])
def get_my_plan():
    """Get current user's plan"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        plan = plan_service.get_founder_plan(clerk_user_id)
        return jsonify(plan), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting plan", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/check-feature', methods=['GET'])
def check_feature():
    """Check if user has access to a feature"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        feature_path = request.args.get('feature')
        if not feature_path:
            return jsonify({"error": "feature parameter required"}), 400
        
        has_access = plan_service.check_feature_access(clerk_user_id, feature_path)
        return jsonify({"has_access": has_access}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error checking feature", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/check-feature', methods=['GET'])
def check_workspace_feature(workspace_id):
    """Check if workspace has access to a feature based on highest plan tier among participants"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Verify user has access to workspace
        workspace_service._verify_workspace_access(clerk_user_id, workspace_id)
        
        feature_path = request.args.get('feature')
        if not feature_path:
            return jsonify({"error": "feature parameter required"}), 400
        
        has_access = plan_service.check_workspace_feature_access(workspace_id, feature_path)
        highest_plan = plan_service.get_workspace_highest_plan(workspace_id)
        
        return jsonify({
            "has_access": has_access,
            "workspace_plan": highest_plan
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error checking workspace feature", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/workspace-limit', methods=['GET'])
def check_workspace_limit():
    """Check workspace creation limit"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        can_create, current_count, max_allowed = plan_service.check_workspace_limit(clerk_user_id)
        return jsonify({
            "can_create": can_create,
            "current_count": current_count,
            "max_allowed": max_allowed,
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error checking workspace limit", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/discovery-limit', methods=['GET'])
def check_discovery_limit():
    """Check discovery swipe limit"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        can_swipe, current_count, max_allowed = plan_service.check_discovery_limit(clerk_user_id)
        return jsonify({
            "can_swipe": can_swipe,
            "current_count": current_count,
            "max_allowed": max_allowed,
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error checking discovery limit", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/founder/subscribe', methods=['POST'])
@limiter.limit(RATE_LIMITS['strict'])
def subscribe_plan():
    """Subscribe to a plan using Polar"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data or 'plan' not in data:
            return jsonify({"error": "plan is required"}), 400
        
        new_plan = data['plan']
        if new_plan not in ['PRO', 'PRO_PLUS']:
            return jsonify({"error": "Invalid plan. Must be PRO or PRO_PLUS"}), 400
        
        # Create Polar checkout session
        checkout = subscription_service.create_subscription_checkout(clerk_user_id, new_plan)
        
        return jsonify(checkout), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating subscription checkout", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/founder/cancel', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def cancel_subscription():
    """Cancel subscription in Polar and downgrade to FREE plan"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        workspace_to_keep = data.get('workspace_to_keep')  # Optional: user can specify which workspace to keep
        
        # Check if user has multiple workspaces that would exceed FREE plan limit
        can_create, current_count, max_allowed = plan_service.check_workspace_limit(clerk_user_id)
        current_plan = plan_service.get_founder_plan(clerk_user_id)
        
        # If downgrading from paid plan and would exceed FREE limit (1 workspace)
        if current_plan.get('id') != 'FREE' and current_count > 1:
            # If user didn't specify which workspace to keep, return list for selection
            if not workspace_to_keep:
                # Get list of user's workspaces
                from services import workspace_service
                workspaces = workspace_service.get_workspaces(clerk_user_id)
                return jsonify({
                    "error": "workspace_selection_required",
                    "message": f"You have {current_count} workspaces. FREE plan allows only 1 workspace. Please select which workspace to keep.",
                    "workspaces": workspaces,
                    "current_count": current_count,
                    "max_allowed": 1
                }), 400
        
        # STEP 1: Cancel subscription in Polar FIRST (to stop billing)
        from services import subscription_service
        try:
            polar_result = subscription_service.cancel_polar_subscription(clerk_user_id)
            log_info(f"Polar subscription cancellation result for {clerk_user_id}: {polar_result}")
        except Exception as polar_error:
            # Log but don't fail - we still want to downgrade the user in our DB
            # This handles edge cases where Polar subscription doesn't exist but user is on paid plan
            log_error(f"Failed to cancel Polar subscription for {clerk_user_id}: {polar_error}")
        
        # STEP 2: Downgrade to FREE in our database
        # Update subscription_status to 'canceled' when downgrading
        updated_plan = plan_service.update_founder_plan(
            clerk_user_id, 
            'FREE', 
            workspace_to_keep=workspace_to_keep,
            subscription_status='canceled'
        )
        
        return jsonify({
            **updated_plan,
            "message": "Subscription cancelled successfully. You have been downgraded to the Free plan."
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error canceling subscription", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/advisor/profile', methods=['GET'])
def get_advisor_billing_profile():
    """Get advisor billing profile"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        profile = plan_service.get_advisor_billing_profile(clerk_user_id)
        return jsonify(profile), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting advisor billing profile", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/advisor/accept-project', methods=['POST'])
def pay_advisor_accept_project():
    """Pay to accept a project - per-project fee"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        request_id = data.get('request_id')
        if not request_id:
            return jsonify({"error": "request_id is required"}), 400
        
        # Create Polar checkout session with request_id
        checkout = subscription_service.create_advisor_project_accept_checkout(clerk_user_id, request_id)
        
        return jsonify(checkout), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating advisor project accept checkout", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/advisor/renewal', methods=['POST'])
def renew_advisor_subscription():
    """Renew advisor annual subscription using Polar"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Create Polar checkout session
        checkout = subscription_service.create_advisor_renewal_checkout(clerk_user_id)
        
        return jsonify(checkout), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating advisor renewal checkout", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/advisor/calculate-pricing', methods=['GET'])
def calculate_advisor_pricing():
    """Calculate advisor pricing breakdown"""
    try:
        monthly_rate = request.args.get('monthly_rate')
        if not monthly_rate:
            return jsonify({"error": "monthly_rate parameter required"}), 400
        
        monthly_rate_usd = float(monthly_rate)
        pricing = plan_service.calculate_advisor_pricing(monthly_rate_usd)
        return jsonify(pricing), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error calculating pricing", error=e)
        return jsonify({"error": str(e)}), 500

# Product Feedback Routes
@app.route('/api/feedback', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def create_feedback():
    """Create a new feedback entry"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        category = data.get('category', 'Other')
        workspace_id = data.get('workspaceId') or data.get('workspace_id')
        
        feedback = feedback_service.create_feedback(
            clerk_user_id=clerk_user_id,
            title=title,
            description=description,
            category=category,
            workspace_id=workspace_id
        )
        
        return jsonify(feedback), 201
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating feedback", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/feedback/my', methods=['GET'])
@limiter.limit(RATE_LIMITS['moderate'])
def get_my_feedback():
    """Get all feedback entries for the current user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        feedback_list = feedback_service.get_user_feedback(clerk_user_id)
        return jsonify(feedback_list), 200
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error fetching feedback", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/feedback/<feedback_id>', methods=['PATCH'])
@limiter.limit(RATE_LIMITS['moderate'])
def update_feedback_admin(feedback_id):
    """Admin-only: Update feedback status and reward fields"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        status = data.get('status')
        usefulness_score = data.get('usefulness_score')
        reward_amount_cents = data.get('reward_amount_cents')
        reward_paid = data.get('reward_paid')
        
        feedback = feedback_service.update_feedback_admin(
            feedback_id=feedback_id,
            status=status,
            usefulness_score=usefulness_score,
            reward_amount_cents=reward_amount_cents,
            reward_paid=reward_paid
        )
        
        return jsonify(feedback), 200
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error updating feedback", error=e)
        return jsonify({"error": str(e)}), 500

# ============================================================================
# Equity Questionnaire API Endpoints
# ============================================================================

from services import equity_questionnaire_service, equity_document_service

@app.route('/api/workspaces/<workspace_id>/equity/questionnaire', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def save_equity_questionnaire(workspace_id):
    """Save or update a founder's equity questionnaire responses"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid data format"}), 400
        
        responses = data.get('responses', {})
        is_complete = data.get('is_complete', False)
        
        # Basic validation
        if not isinstance(responses, dict):
            return jsonify({"error": "responses must be an object"}), 400
        if not isinstance(is_complete, bool):
            return jsonify({"error": "is_complete must be a boolean"}), 400
        
        log_info(f"save_equity_questionnaire: workspace={workspace_id}, is_complete={is_complete}, responses_keys={list(responses.keys())}")
        
        result = equity_questionnaire_service.save_questionnaire_response(
            clerk_user_id, workspace_id, responses, is_complete
        )
        
        log_info(f"save_equity_questionnaire result: is_complete in result = {result.get('is_complete')}")
        
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error saving equity questionnaire", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/questionnaire', methods=['GET'])
def get_equity_questionnaire(workspace_id):
    """Get all questionnaire responses for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = equity_questionnaire_service.get_questionnaire_responses(
            clerk_user_id, workspace_id
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log_error("Error getting equity questionnaire", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/startup-context', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def save_startup_context(workspace_id):
    """Save startup context (Stage, Idea Origin, IP)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        log_info(f"Startup context POST data: {data}")
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Extract startup_context from the request body
        startup_context = data.get('startup_context', data)
        log_info(f"Extracted startup_context: {startup_context}")
        
        if not startup_context:
            return jsonify({"error": "No startup context provided"}), 400
        
        result = equity_questionnaire_service.save_startup_context(
            clerk_user_id, workspace_id, startup_context
        )
        return jsonify(result), 200
    except ValueError as e:
        log_error(f"ValueError saving startup context: {str(e)}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error saving startup context", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/startup-context', methods=['GET'])
def get_startup_context(workspace_id):
    """Get startup context"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = equity_questionnaire_service.get_startup_context(
            clerk_user_id, workspace_id
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log_error("Error getting startup context", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/calculate', methods=['POST'])
def calculate_equity(workspace_id):
    """Calculate equity scenarios based on questionnaire responses"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Get optional advisor_percent from request body (uses current UI state)
        data = request.get_json(silent=True) or {}
        override_advisor_percent = data.get('advisor_percent')
        
        result = equity_questionnaire_service.calculate_equity(
            clerk_user_id, workspace_id, override_advisor_percent
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error calculating equity", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/scenarios', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def create_new_equity_scenario(workspace_id):
    """Create an equity scenario from a selected option"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        scenario_type = data.get('scenario_type')
        founder_a_percent = data.get('founder_a_percent')
        founder_b_percent = data.get('founder_b_percent')
        vesting_terms = data.get('vesting_terms')
        calculation_breakdown = data.get('calculation_breakdown')
        advisor_percent = data.get('advisor_percent')  # Advisor equity allocation
        
        if not scenario_type or founder_a_percent is None or founder_b_percent is None:
            return jsonify({"error": "scenario_type, founder_a_percent, and founder_b_percent are required"}), 400
        
        result = equity_questionnaire_service.create_equity_scenario(
            clerk_user_id, workspace_id,
            scenario_type, founder_a_percent, founder_b_percent,
            vesting_terms, calculation_breakdown, advisor_percent
        )
        # Wrap in scenario key for frontend compatibility
        return jsonify({"scenario": result}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating equity scenario", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/scenarios', methods=['GET'])
def get_new_equity_scenarios(workspace_id):
    """Get all equity scenarios for a workspace (new system)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = equity_questionnaire_service.get_equity_scenarios(
            clerk_user_id, workspace_id
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log_error("Error getting equity scenarios", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/scenarios/<scenario_id>/approve', methods=['PATCH'])
@limiter.limit(RATE_LIMITS['moderate'])
def approve_equity_scenario(workspace_id, scenario_id):
    """Record approval for a scenario by the current user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = equity_questionnaire_service.approve_scenario(
            clerk_user_id, workspace_id, scenario_id
        )
        # Wrap in scenario key for frontend compatibility
        return jsonify({"scenario": result}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error approving equity scenario", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/scenarios/<scenario_id>/reject', methods=['PATCH'])
@limiter.limit(RATE_LIMITS['moderate'])
def reject_equity_scenario(workspace_id, scenario_id):
    """Reject a scenario"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        reason = data.get('reason')
        
        result = equity_questionnaire_service.reject_scenario(
            clerk_user_id, workspace_id, scenario_id, reason
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error rejecting equity scenario", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/vesting', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def update_equity_vesting(workspace_id):
    """Update vesting terms"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        result = equity_questionnaire_service.update_vesting_terms(
            clerk_user_id, workspace_id, data
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error updating vesting terms", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/generate-document', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def generate_equity_document(workspace_id):
    """Generate agreement document (PDF/DOCX)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json() or {}
        scenario_id = data.get('scenario_id')
        
        result = equity_document_service.generate_and_save_document(
            clerk_user_id, workspace_id, scenario_id
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error generating equity document", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/documents', methods=['GET'])
def list_equity_documents(workspace_id):
    """List all generated equity documents for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = equity_document_service.list_documents(clerk_user_id, workspace_id)
        return jsonify({"documents": result}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log_error("Error listing equity documents", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/documents/<document_id>', methods=['GET'])
def get_equity_document(workspace_id, document_id):
    """Get a specific equity document with signed URLs"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = equity_document_service.get_document(clerk_user_id, workspace_id, document_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log_error("Error getting equity document", error=e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/workspaces/<workspace_id>/equity/documents/<document_id>/download/<file_type>', methods=['GET'])
def download_equity_document(workspace_id, document_id, file_type):
    """
    Proxy download endpoint for equity documents.
    Downloads the file server-side and streams it to the client,
    avoiding exposure of Supabase signed URLs.
    
    Args:
        workspace_id: Workspace ID
        document_id: Document ID
        file_type: 'pdf' or 'docx'
    """
    from flask import Response
    
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Validate file type
        if file_type not in ['pdf', 'docx']:
            return jsonify({"error": "Invalid file type. Use 'pdf' or 'docx'"}), 400
        
        # Download file content
        file_content, content_type, filename = equity_document_service.download_document(
            clerk_user_id, workspace_id, document_id, file_type
        )
        
        # Return file as response
        response = Response(
            file_content,
            mimetype=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(len(file_content)),
                'Cache-Control': 'private, max-age=3600'
            }
        )
        return response
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log_error("Error downloading equity document", error=e)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)