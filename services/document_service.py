"""Workspace document management service"""
import uuid
import os
from config.database import get_supabase, get_supabase_admin
from services.workspace_service import _get_founder_id, _verify_workspace_access
from utils.logger import log_error, log_info

# Allowed file types
ALLOWED_EXTENSIONS = {'.pdf', '.xlsx', '.xls', '.csv'}
ALLOWED_MIME_TYPES = {
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
    'application/vnd.ms-excel',  # .xls
    'text/csv',
    'application/vnd.ms-excel'  # CSV can also be this
}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

def _verify_founder_access(clerk_user_id, workspace_id):
    """Verify user is a founder/owner (not accountability partner)"""
    founder_id = _get_founder_id(clerk_user_id)
    supabase = get_supabase()
    
    try:
        participant = supabase.table('workspace_participants').select('role').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
    except Exception:
        # Fallback if role column doesn't exist yet - assume founder
        participant = supabase.table('workspace_participants').select('id').eq(
            'workspace_id', workspace_id
        ).eq('user_id', founder_id).execute()
    
    if not participant.data:
        raise ValueError("Access denied: You are not a participant in this workspace")
    
    role = participant.data[0].get('role')
    # If role is None/not set, treat as founder (has all permissions)
    # Only ACCOUNTABILITY_PARTNER cannot upload/delete
    if role == 'ACCOUNTABILITY_PARTNER':
        raise ValueError("Access denied: Accountability partners cannot manage documents")
    
    return founder_id

def _sanitize_filename(filename):
    """Sanitize filename to prevent path traversal"""
    # Remove any path components
    filename = os.path.basename(filename)
    # Remove any dangerous characters but preserve common filename characters
    # Allow alphanumeric, spaces, dots, underscores, hyphens, parentheses
    filename = ''.join(c for c in filename if c.isalnum() or c in ' ._-()')
    # Remove leading/trailing spaces and dots
    filename = filename.strip(' .')
    # Limit length
    return filename[:255] if len(filename) > 255 else filename

def _validate_file(file):
    """Validate uploaded file"""
    if not file:
        raise ValueError("No file provided")
    
    # Check file size
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)  # Reset to beginning
    
    if size > MAX_FILE_SIZE:
        raise ValueError(f"File size exceeds maximum of {MAX_FILE_SIZE / (1024 * 1024)} MB")
    
    if size == 0:
        raise ValueError("File is empty")
    
    # Check extension
    filename = file.filename if hasattr(file, 'filename') else ''
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type not allowed. Allowed types: PDF, Excel (.xlsx, .xls), CSV")
    
    # Check MIME type if available
    content_type = getattr(file, 'content_type', None) or getattr(file, 'mimetype', None)
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        # Allow if extension is valid (some browsers send wrong MIME types)
        pass
    
    return ext, size, content_type

def upload_document(clerk_user_id, workspace_id, file, category=None, description=None):
    """Upload a document to workspace storage"""
    founder_id = _verify_founder_access(clerk_user_id, workspace_id)
    supabase = get_supabase()
    # Use admin client for storage operations to bypass RLS
    supabase_storage = get_supabase_admin()
    is_using_admin = supabase_storage is not None
    if not supabase_storage:
        log_error("WARNING: SERVICE_ROLE_KEY not set - storage operations may fail due to RLS policies")
        log_error("Please set SUPABASE_SERVICE_ROLE_KEY in your environment variables")
        supabase_storage = supabase  # Fallback to regular client
    else:
        log_info("Using admin client (service role key) for storage upload - RLS bypassed")
    
    # Validate file
    ext, size, content_type = _validate_file(file)
    
    # Validate category
    valid_categories = ['General', 'Legal', 'Financial', 'Product', 'Hiring']
    if category and category not in valid_categories:
        raise ValueError(f"Invalid category. Must be one of: {', '.join(valid_categories)}")
    
    category = category or 'General'
    
    # Generate unique storage path
    original_filename = file.filename if hasattr(file, 'filename') else 'document'
    sanitized_filename = _sanitize_filename(original_filename)
    unique_id = str(uuid.uuid4())
    storage_path = f"{workspace_id}/{unique_id}-{sanitized_filename}"
    
    # Read file content
    file.seek(0)
    file_content = file.read()
    
    # Upload to Supabase Storage
    try:
        # Ensure file_content is bytes
        if isinstance(file_content, str):
            file_content = file_content.encode('utf-8')
        
        # Determine content type based on file extension if not provided
        if not content_type:
            ext = os.path.splitext(original_filename)[1].lower()
            content_type_map = {
                '.pdf': 'application/pdf',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.xls': 'application/vnd.ms-excel',
                '.csv': 'text/csv'
            }
            content_type = content_type_map.get(ext, 'application/octet-stream')
        
        # Upload with proper content type
        # Note: Supabase Python SDK upload method signature
        log_info(f"Uploading file to storage: {storage_path}, size: {len(file_content)} bytes, content-type: {content_type}")
        log_info(f"Using {'admin' if supabase_storage != supabase else 'anon'} client for storage upload")
        
        # Upload file - Use admin client for storage to bypass RLS policies
        # Supabase Python SDK expects: upload(path, file_bytes, file_options)
        try:
            storage_response = supabase_storage.storage.from_('workspace-documents').upload(
                storage_path,
                file_content,
                file_options={
                    'content-type': content_type,
                    'upsert': False
                }
            )
        except Exception as upload_error:
            # Log the full error for debugging
            log_error(f"Storage upload error: {str(upload_error)}")
            log_error(f"Error type: {type(upload_error).__name__}")
            # Check if it's an RLS error and admin client wasn't used
            if 'row-level security' in str(upload_error).lower() and supabase_storage == supabase:
                raise ValueError(
                    "Storage upload failed due to RLS policies. "
                    "Please set SUPABASE_SERVICE_ROLE_KEY in your environment variables. "
                    "Get it from Supabase Dashboard → Settings → API → service_role key"
                )
            raise
        
        # Handle response - Supabase Python SDK returns dict or object
        if isinstance(storage_response, dict):
            if storage_response.get('error'):
                error_msg = storage_response['error']
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get('message', str(error_msg))
                raise ValueError(f"Storage upload failed: {error_msg}")
        elif hasattr(storage_response, 'error') and storage_response.error:
            error_msg = storage_response.error
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise ValueError(f"Storage upload failed: {error_msg}")
    except ValueError:
        raise
    except Exception as e:
        # Check if it's a StorageApiError from Supabase
        error_class_name = type(e).__name__
        error_str = str(e).lower()
        
        # Check for RLS policy violation
        if 'row-level security' in error_str or 'rls' in error_str or '403' in error_str:
            if supabase_storage == supabase:
                raise ValueError(
                    "Storage upload failed due to RLS policies. "
                    "Please set SUPABASE_SERVICE_ROLE_KEY in your environment variables.\n\n"
                    "To get your service role key:\n"
                    "1. Go to Supabase Dashboard → Settings → API\n"
                    "2. Copy the 'service_role' key (secret, not the anon key)\n"
                    "3. Add to your .env file: SUPABASE_SERVICE_ROLE_KEY=your_key_here\n"
                    "4. Restart your backend server"
                )
            else:
                raise ValueError(
                    f"Storage upload failed due to RLS policies even with admin client. "
                    f"Error: {str(e)}\n\n"
                    "This might be a storage bucket policy issue. "
                    "Try checking Storage → Policies in Supabase Dashboard."
                )
        
        if 'StorageApiError' in error_class_name or 'bucket not found' in error_str:
            raise ValueError(
                "Storage bucket 'workspace-documents' not found. "
                "Please create the bucket in Supabase Dashboard:\n"
                "1. Go to Storage in your Supabase Dashboard\n"
                "2. Click 'New bucket'\n"
                "3. Name it 'workspace-documents'\n"
                "4. Set it to 'Private' (not public)\n"
                "5. Save the bucket"
            )
        
        error_msg = str(e)
        # Try to extract more details from the exception
        if hasattr(e, 'message'):
            error_msg = e.message
        elif hasattr(e, 'args') and e.args:
            error_msg = str(e.args[0])
        
        # Log the full exception for debugging
        import traceback
        traceback.print_exc()
        raise ValueError(f"Failed to upload file to storage: {error_msg}")
    
    # Insert metadata into database
    # Use admin client to bypass RLS policies if available
    try:
        document_data = {
            'workspace_id': workspace_id,
            'uploaded_by': founder_id,
            'original_filename': original_filename,
            'storage_path': storage_path,
            'mime_type': content_type or 'application/octet-stream',
            'size_bytes': size,
            'category': category,
            'description': description
        }
        
        # Use admin client for database insert to bypass RLS
        db_client = get_supabase_admin() or supabase
        result = db_client.table('workspace_documents').insert(document_data).execute()
        
        if not result.data:
            # If DB insert fails, try to clean up storage
            try:
                supabase_storage.storage.from_('workspace-documents').remove([storage_path])
            except:
                pass
            raise ValueError("Failed to save document metadata")
        
        return result.data[0]
    except Exception as e:
        # If DB insert fails, try to clean up storage
        try:
            supabase_storage.storage.from_('workspace-documents').remove([storage_path])
        except:
            pass
        raise ValueError(f"Failed to save document: {str(e)}")

def list_documents(clerk_user_id, workspace_id, category=None, search=None):
    """List documents for a workspace"""
    _verify_workspace_access(clerk_user_id, workspace_id)  # Any member can read
    # Use admin client to bypass RLS since we're using Clerk (not Supabase Auth)
    supabase = get_supabase_admin() or get_supabase()
    
    query = supabase.table('workspace_documents').select(
        'id, original_filename, category, uploaded_by, created_at, size_bytes, description'
    ).eq('workspace_id', workspace_id)
    
    # Filter by category if provided
    if category:
        valid_categories = ['General', 'Legal', 'Financial', 'Product', 'Hiring']
        if category not in valid_categories:
            raise ValueError(f"Invalid category. Must be one of: {', '.join(valid_categories)}")
        query = query.eq('category', category)
    
    # Search by filename or description
    if search:
        # Supabase doesn't support OR easily, so we'll filter in Python
        # For now, search in filename
        query = query.ilike('original_filename', f'%{search}%')
    
    result = query.order('created_at', desc=True).execute()
    
    documents = []
    for doc in (result.data or []):
        documents.append({
            'id': doc['id'],
            'original_filename': doc['original_filename'],
            'category': doc['category'],
            'uploaded_by': doc['uploaded_by'],
            'created_at': doc['created_at'],
            'size_bytes': doc['size_bytes'],
            'description': doc.get('description')
        })
    
    # If search was provided, also search in description
    if search and documents:
        filtered = []
        search_lower = search.lower()
        for doc in documents:
            if (search_lower in doc['original_filename'].lower() or 
                (doc.get('description') and search_lower in doc['description'].lower())):
                filtered.append(doc)
        documents = filtered
    
    return documents

def get_document_signed_url(clerk_user_id, workspace_id, document_id):
    """Generate a signed URL for downloading a document"""
    _verify_workspace_access(clerk_user_id, workspace_id)  # Any member can download
    # Use admin client to bypass RLS since we're using Clerk (not Supabase Auth)
    supabase = get_supabase_admin() or get_supabase()
    
    # Fetch document metadata
    result = supabase.table('workspace_documents').select('storage_path, workspace_id').eq(
        'id', document_id
    ).eq('workspace_id', workspace_id).execute()
    
    if not result.data:
        raise ValueError("Document not found")
    
    storage_path = result.data[0]['storage_path']
    
    # Generate signed URL (10 minutes TTL)
    try:
        signed_url_response = supabase.storage.from_('workspace-documents').create_signed_url(
            storage_path,
            600  # 10 minutes in seconds
        )
        
        # Handle response - Supabase Python SDK returns a dict with 'signedUrl' key
        if isinstance(signed_url_response, dict):
            signed_url = signed_url_response.get('signedUrl')
            if not signed_url:
                # Try alternative response formats
                signed_url = signed_url_response.get('data', {}).get('signedUrl') if isinstance(signed_url_response.get('data'), dict) else None
        elif hasattr(signed_url_response, 'signedUrl'):
            signed_url = signed_url_response.signedUrl
        elif hasattr(signed_url_response, 'data'):
            if isinstance(signed_url_response.data, dict):
                signed_url = signed_url_response.data.get('signedUrl')
            else:
                signed_url = None
        else:
            signed_url = None
        
        # Check for errors
        if isinstance(signed_url_response, dict) and signed_url_response.get('error'):
            raise ValueError(f"Failed to generate signed URL: {signed_url_response['error']}")
        
        if not signed_url:
            raise ValueError("Failed to generate signed URL: invalid response format")
        
        return {'url': signed_url}
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to generate signed URL: {str(e)}")

def delete_document(clerk_user_id, workspace_id, document_id):
    """Delete a document and its stored file"""
    _verify_founder_access(clerk_user_id, workspace_id)  # Only founders can delete
    # Use admin client to bypass RLS since we're using Clerk (not Supabase Auth)
    supabase = get_supabase_admin() or get_supabase()
    # Use admin client for storage operations to bypass RLS
    supabase_storage = get_supabase_admin() or get_supabase()
    
    # Fetch document metadata
    result = supabase.table('workspace_documents').select('storage_path, workspace_id').eq(
        'id', document_id
    ).eq('workspace_id', workspace_id).execute()
    
    if not result.data:
        raise ValueError("Document not found")
    
    storage_path = result.data[0]['storage_path']
    
    # Delete from database first
    delete_result = supabase.table('workspace_documents').delete().eq('id', document_id).execute()
    
    # Delete from storage (even if DB delete fails, try to clean up storage)
    try:
        storage_delete_response = supabase_storage.storage.from_('workspace-documents').remove([storage_path])
        if isinstance(storage_delete_response, dict) and storage_delete_response.get('error'):
            # Log but don't fail - DB record is already deleted
            pass
        elif hasattr(storage_delete_response, 'error') and storage_delete_response.error:
            # Log but don't fail - DB record is already deleted
            pass
    except Exception as e:
        # Log but don't fail - DB record is already deleted
        pass
    
    return {'success': True, 'message': 'Document deleted successfully'}

