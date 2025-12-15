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
from services import founder_service, project_service, swipe_service, profile_service, match_service, waitlist_service, message_service, payment_service, workspace_service, task_service, accountability_partner_service
from services import plan_service, subscription_service
from services.notification_service import NotificationService, ApprovalService

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": ["http://localhost:3000", "http://127.0.0.1:5000", "https://founder-match.in/"],  # Allow iOS simulator
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        "allow_headers": ["Content-Type", "X-Clerk-User-Id", "X-User-Email", "X-User-Name"],
        "supports_credentials": True
    }
})

# Initialize rate limiter
limiter = init_rate_limiter(app)

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
        
        # Check if founder exists
        existing = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        
        if existing.data:
            # Update existing founder
            founder_id = existing.data[0]['id']
            update_data = {
                'purpose': validated_data['purpose'],
                'location': validated_data['location'],
                'skills': validated_data['skills'],
                'onboarding_completed': True
            }
            
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
                
                for idx, project in enumerate(data['projects'][:10]):  # Limit to 10 projects
                    title = sanitize_string(project.get('title'), max_length=200)
                    description = sanitize_string(project.get('description'), max_length=5000)
                    stage = validate_enum(project.get('stage', 'idea'), 
                                         ['idea', 'mvp', 'early_revenue', 'scaling'], 
                                         case_sensitive=False) or 'idea'
                    
                    if title and description:
                        try:
                            project_data = {
                                'founder_id': founder_id,
                                'title': title,
                                'description': description,
                                'stage': stage,
                                'display_order': max_order + idx + 1
                            }
                            supabase.table('projects').insert(project_data).execute()
                        except Exception as project_error:
                            # Log but don't fail if project insert fails (might be duplicate)
                            log_warning(f"Failed to insert project: {str(project_error)}")
            
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
                'onboarding_completed': True,
                'credits': 10  # Give initial credits
            }
            
            result = supabase.table('founders').insert(founder_data).execute()
            
            if not result.data:
                return jsonify({"error": "Failed to create founder"}), 500
            
            founder_id = result.data[0]['id']
            
            # Add projects if provided
            if data.get('projects'):
                for idx, project in enumerate(data['projects']):
                    if project.get('title') and project.get('description'):
                        try:
                            project_data = {
                                'founder_id': founder_id,
                                'title': project['title'],
                                'description': project['description'],
                                'stage': project.get('stage', 'idea'),
                                'display_order': idx
                            }
                            supabase.table('projects').insert(project_data).execute()
                        except Exception as project_error:
                            # Log but don't fail if project insert fails
                            log_warning(f"Failed to insert project: {str(project_error)}")
            
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

@app.route('/api/founders', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def create_founder():
    """Create a new founder profile with projects"""
    try:
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
def get_my_projects():
    """Get all projects for the current logged-in user"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        projects = project_service.get_user_projects(clerk_user_id)
        return jsonify(projects), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in get_my_projects", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/projects', methods=['POST'])
@limiter.limit(RATE_LIMITS['moderate'])
def create_project():
    """Create a new project for a founder - deducts 5 credits"""
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
    """Record a swipe action - deducts 2 credits for right swipes"""
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

@app.route('/api/profile/credits', methods=['GET'])
def get_credits():
    """Get current user's credits"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = profile_service.get_credits(clerk_user_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error in get_credits", error=e, traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/profile/debug', methods=['GET'])
def debug_profile():
    """Debug endpoint to check user profile and credits"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = profile_service.debug_profile(clerk_user_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        error_trace = traceback.format_exc()
        return jsonify({"error": str(e), "traceback": error_trace}), 500

@app.route('/api/matches', methods=['GET'])
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

@app.route('/api/payments/create-checkout', methods=['POST'])
@limiter.limit(RATE_LIMITS['strict'])
def create_checkout():
    """Create a Polar checkout session for purchasing credits"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        product_id = data.get('product_id')
        credits_amount = data.get('credits_amount')
        
        if not product_id or not credits_amount:
            return jsonify({"error": "product_id and credits_amount are required"}), 400
        
        result = payment_service.create_checkout_session(clerk_user_id, product_id, credits_amount)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error_trace = traceback.format_exc()
        log_error("Error creating checkout", traceback_str=error_trace)
        return jsonify({"error": str(e)}), 500

@app.route('/api/payments/webhook', methods=['POST'])
@limiter.limit(RATE_LIMITS['strict'])
def handle_webhook():
    """Handle Polar webhook events for credits (legacy)"""
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
    """Handle Polar webhook events for subscriptions"""
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
        
        if not subscription_service.verify_webhook_signature(payload, signature):
            return jsonify({"error": "Invalid webhook signature"}), 401
        
        # Handle subscription webhook event
        result = subscription_service.handle_subscription_webhook(webhook_data)
        
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
        
        # Get founder ID
        supabase = get_supabase()
        founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not founder.data:
            return jsonify({"error": "Founder not found"}), 404
        
        founder_id = founder.data[0]['id']
        
        # Get summaries for each workspace
        summaries = {}
        for workspace_id in workspace_ids:
            # Get pending approvals count
            approvals = supabase.table('approvals').select('id').eq(
                'workspace_id', workspace_id
            ).eq('approver_user_id', founder_id).eq('status', 'PENDING').execute()
            
            # Get unread notifications count
            # A notification is considered "unread" when read_at IS NULL
            # This ensures the badge only shows when there are actually unread notifications
            notifications = supabase.table('notifications').select('id').eq(
                'user_id', founder_id
            ).eq('workspace_id', workspace_id).is_('read_at', 'null').execute()
            
            summaries[workspace_id] = {
                'pending_approvals': len(approvals.data) if approvals.data else 0,
                'unread_updates': len(notifications.data) if notifications.data else 0
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
        
        # Get founder ID
        supabase = get_supabase()
        founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not founder.data:
            return jsonify({"error": "Founder not found"}), 404
        
        founder_id = founder.data[0]['id']
        
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
        
        # Get founder ID
        supabase = get_supabase()
        founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not founder.data:
            return jsonify({"error": "Founder not found"}), 404
        
        founder_id = founder.data[0]['id']
        
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
        
        # Get founder ID
        supabase = get_supabase()
        founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not founder.data:
            return jsonify({"error": "Founder not found"}), 404
        
        founder_id = founder.data[0]['id']
        
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

@app.route('/api/debug/approvals/<workspace_id>', methods=['GET'])
def debug_approvals(workspace_id):
    """Debug endpoint to check all approvals for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        supabase = get_supabase()
        
        # Get founder ID
        founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        founder_id = founder.data[0]['id'] if founder.data else None
        
        # Get all approvals for workspace
        all_approvals = supabase.table('approvals').select('*').eq(
            'workspace_id', workspace_id
        ).execute()
        
        # Get pending approvals where user is approver
        pending_as_approver = supabase.table('approvals').select('*').eq(
            'workspace_id', workspace_id
        ).eq('approver_user_id', founder_id).eq('status', 'PENDING').execute() if founder_id else None
        
        # Get pending approvals where user is proposer
        pending_as_proposer = supabase.table('approvals').select('*').eq(
            'workspace_id', workspace_id
        ).eq('proposed_by_user_id', founder_id).eq('status', 'PENDING').execute() if founder_id else None
        
        # Get all notifications for workspace
        notifications = supabase.table('notifications').select('*').eq(
            'workspace_id', workspace_id
        ).execute()
        
        return jsonify({
            'current_user_founder_id': founder_id,
            'all_approvals': all_approvals.data or [],
            'pending_as_approver': pending_as_approver.data if pending_as_approver else [],
            'pending_as_proposer': pending_as_proposer.data if pending_as_proposer else [],
            'notifications': notifications.data or [],
            'counts': {
                'total_approvals': len(all_approvals.data) if all_approvals.data else 0,
                'pending_for_you': len(pending_as_approver.data) if pending_as_approver and pending_as_approver.data else 0,
                'pending_from_you': len(pending_as_proposer.data) if pending_as_proposer and pending_as_proposer.data else 0,
                'notifications': len(notifications.data) if notifications.data else 0
            }
        })
        
    except Exception as e:
        log_error("Error in debug", error=e)
        return jsonify({"error": str(e)}), 500

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
        
        # Get founder ID
        supabase = get_supabase()
        founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not founder.data:
            return jsonify({"error": "Founder not found"}), 404
        
        founder_id = founder.data[0]['id']
        
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

# ==================== ACCOUNTABILITY PARTNER ENDPOINTS ====================

@app.route('/api/accountability-partners/profile', methods=['GET', 'POST', 'PUT'])
def partner_profile():
    """Get, create, or update accountability partner profile"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        if request.method == 'GET':
            profile = accountability_partner_service.get_partner_profile(clerk_user_id)
            if not profile:
                return jsonify({"error": "Partner profile not found"}), 404
            return jsonify(profile), 200
        else:  # POST or PUT
            data = request.get_json()
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            log_info(f"Creating partner profile for clerk_user_id: {clerk_user_id}")
            log_info(f"Request data: {data}")
            
            # Get user name and email from request if available (for creating minimal founder profile)
            user_name = data.get('user_name') or request.headers.get('X-User-Name')
            user_email = data.get('user_email') or request.headers.get('X-User-Email')
            
            log_info(f"User name: {user_name}, User email: {user_email}")
            
            # Handle contact info updates separately if provided
            contact_info = {}
            if 'contact_email' in data:
                contact_info['contact_email'] = data.pop('contact_email')
            if 'meeting_link' in data:
                contact_info['meeting_link'] = data.pop('meeting_link')
            if 'contact_note' in data:
                contact_info['contact_note'] = data.pop('contact_note')
            
            profile = accountability_partner_service.create_partner_profile(
                clerk_user_id, 
                data, 
                user_name=user_name, 
                user_email=user_email
            )
            
            # Update contact info if provided
            if contact_info:
                accountability_partner_service.update_partner_contact_info(clerk_user_id, contact_info)
                # Refresh profile to include contact info
                profile = accountability_partner_service.get_partner_profile(clerk_user_id)
            
            log_info(f"Partner profile created successfully: {profile.get('id')}")
            return jsonify(profile), 201 if request.method == 'POST' else 200
    except ValueError as e:
        error_msg = str(e)
        log_error(f"ValueError in partner_profile: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": error_msg}), 400
    except Exception as e:
        error_msg = str(e)
        error_trace = traceback.format_exc()
        log_error(f"Error with partner profile: {error_msg}", traceback_str=error_trace)
        return jsonify({
            "error": error_msg,
            "traceback": error_trace if app.debug else None
        }), 500

@app.route('/api/workspaces/<workspace_id>/accountability-partners/marketplace', methods=['GET'])
def get_partner_marketplace(workspace_id):
    """Get available partners for marketplace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        filters = {
            'domain': request.args.get('domain')
        }
        
        partners = accountability_partner_service.get_available_partners(workspace_id, filters, clerk_user_id)
        return jsonify(partners), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting marketplace partners", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/accountability-partners/invite', methods=['POST'])
def invite_own_partner(workspace_id):
    """Invite own accountability partner by email"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data or not data.get('email'):
            return jsonify({"error": "email is required"}), 400
        
        result = accountability_partner_service.invite_own_partner(
            clerk_user_id, workspace_id, data['email'], data.get('name'), data.get('note')
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error inviting partner", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/accountability-partners/request', methods=['POST'])
def create_partner_request(workspace_id):
    """Create a partner request from marketplace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data or not data.get('partner_user_id'):
            return jsonify({"error": "partner_user_id is required"}), 400
        
        request_data = accountability_partner_service.create_partner_request(
            clerk_user_id, workspace_id, data['partner_user_id']
        )
        return jsonify(request_data), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating partner request", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/accountability-partners/requests', methods=['GET'])
def get_partner_requests():
    """Get partner requests for current user (as partner)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        status = request.args.get('status')  # PENDING, ACCEPTED, DECLINED
        requests = accountability_partner_service.get_partner_requests(clerk_user_id, status)
        return jsonify(requests), 200
    except Exception as e:
        log_error("Error getting partner requests", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/accountability-partners/requests/<request_id>/respond', methods=['POST'])
def respond_to_partner_request(request_id):
    """Accept or decline a partner request"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data or not data.get('response'):
            return jsonify({"error": "response is required (accept or decline)"}), 400
        
        result = accountability_partner_service.respond_to_partner_request(
            clerk_user_id, request_id, data['response']
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error responding to partner request", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/accountability-partners/workspaces', methods=['GET'])
def get_partner_workspaces():
    """Get active workspaces for current user (as partner)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        workspaces = accountability_partner_service.get_active_workspaces(clerk_user_id)
        return jsonify(workspaces), 200
    except Exception as e:
        log_error("Error getting partner workspaces", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/accountability-partners/notifications', methods=['GET'])
def get_partner_notifications():
    """Get all notifications for partner across all workspaces"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Get founder ID
        supabase = get_supabase()
        founder = supabase.table('founders').select('id').eq('clerk_user_id', clerk_user_id).execute()
        if not founder.data:
            return jsonify({"error": "Founder not found"}), 404
        
        founder_id = founder.data[0]['id']
        
        # Get all workspaces where user is a partner
        try:
            participants = supabase.table('workspace_participants').select('workspace_id').eq(
                'user_id', founder_id
            ).eq('role', 'ACCOUNTABILITY_PARTNER').execute()
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
        log_error("Error getting partner notifications", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/accountability-partners/<partner_user_id>', methods=['DELETE'])
def remove_partner(workspace_id, partner_user_id):
    """Remove a partner from workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        result = accountability_partner_service.remove_partner_from_workspace(
            clerk_user_id, workspace_id, partner_user_id
        )
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error removing partner", error=e)
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
    """Set verdict for a check-in (partners only)"""
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

@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/partner-review', methods=['GET'])
def get_checkin_partner_review(workspace_id, checkin_id):
    """Get partner review for a check-in (partners only)"""
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
        log_error("Error getting check-in partner review", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/partner-review', methods=['POST'])
def upsert_checkin_partner_review(workspace_id, checkin_id):
    """Create or update partner review for a check-in (partners only)"""
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
        log_error("Error saving check-in partner review", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/checkins/<checkin_id>/partner-reviews', methods=['GET'])
def get_checkin_partner_reviews(workspace_id, checkin_id):
    """Get all partner reviews for a check-in (founders can view)"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        reviews = workspace_service.get_checkin_partner_reviews_for_founders(clerk_user_id, workspace_id, checkin_id)
        return jsonify(reviews), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting check-in partner reviews", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/workspaces/<workspace_id>/partner-impact-scorecard', methods=['GET'])
def get_partner_impact_scorecard(workspace_id):
    """Get partner impact scorecard for a workspace"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        partner_user_id = request.args.get('partner_user_id')  # Optional
        scorecard = accountability_partner_service.compute_partner_impact_scorecard(
            clerk_user_id, workspace_id, partner_user_id
        )
        return jsonify(scorecard), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error computing partner impact scorecard", error=e)
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
        
        partner_user_id = data.get('partner_user_id')
        quarter = data.get('quarter')
        value_rating = data.get('value_rating')
        continue_next_quarter = data.get('continue_next_quarter')
        
        if not all([partner_user_id, quarter, value_rating is not None, continue_next_quarter is not None]):
            return jsonify({"error": "partner_user_id, quarter, value_rating, and continue_next_quarter are required"}), 400
        
        review = accountability_partner_service.save_quarterly_review(
            clerk_user_id, workspace_id, partner_user_id, quarter, value_rating, continue_next_quarter
        )
        return jsonify(review), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error saving quarterly review", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/accountability-partners/profile/contact', methods=['PUT'])
def update_partner_contact():
    """Update partner contact info"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        result = accountability_partner_service.update_partner_contact_info(clerk_user_id, data)
        if result:
            return jsonify(result), 200
        else:
            return jsonify({"error": "Failed to update contact info"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error updating partner contact info", error=e)
        return jsonify({"error": str(e)}), 500

# ==================== BILLING & PLAN ENDPOINTS ====================

@app.route('/api/billing/plans', methods=['GET'])
def get_plans():
    """Get all available founder plans"""
    try:
        return jsonify({
            'founder_plans': plan_service.FOUNDER_PLANS,
            'partner_pricing': plan_service.PARTNER_PRICING,
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
    """Cancel subscription (downgrade to FREE)"""
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
        
        # Downgrade to FREE (will auto-handle workspace selection if needed)
        updated_plan = plan_service.update_founder_plan(clerk_user_id, 'FREE', workspace_to_keep=workspace_to_keep)
        
        return jsonify(updated_plan), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error canceling subscription", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/partner/profile', methods=['GET'])
def get_partner_billing():
    """Get partner billing profile"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        profile = plan_service.get_partner_billing_profile(clerk_user_id)
        return jsonify(profile), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error getting partner billing", error=e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/partner/onboarding', methods=['POST'])
def pay_partner_onboarding():
    """Pay partner onboarding fee using Polar"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Create Polar checkout session
        checkout = subscription_service.create_partner_onboarding_checkout(clerk_user_id)
        
        return jsonify(checkout), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating partner onboarding checkout", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/partner/renewal', methods=['POST'])
def renew_partner_subscription():
    """Renew partner annual subscription using Polar"""
    try:
        clerk_user_id = get_clerk_user_id()
        if not clerk_user_id:
            return jsonify({"error": "User ID required"}), 401
        
        # Create Polar checkout session
        checkout = subscription_service.create_partner_renewal_checkout(clerk_user_id)
        
        return jsonify(checkout), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error creating partner renewal checkout", error=e)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/billing/partner/calculate-pricing', methods=['GET'])
def calculate_partner_pricing():
    """Calculate partner pricing breakdown"""
    try:
        monthly_rate = request.args.get('monthly_rate')
        if not monthly_rate:
            return jsonify({"error": "monthly_rate parameter required"}), 400
        
        monthly_rate_usd = float(monthly_rate)
        pricing = plan_service.calculate_partner_pricing(monthly_rate_usd)
        return jsonify(pricing), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log_error("Error calculating pricing", error=e)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
