"""Image upload service using Supabase Storage"""
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import base64

from config.database import get_supabase
from utils.logger import log_info, log_error

ADVISOR_BUCKET = 'advisor-profiles'
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']


def upload_advisor_profile_image(
    advisor_profile_id: str,
    file_data: bytes,
    content_type: str,
    filename: Optional[str] = None
) -> Dict[str, Any]:
    """
    Upload an advisor profile image to Supabase Storage.
    
    Args:
        advisor_profile_id: The advisor profile ID
        file_data: Raw file bytes
        content_type: MIME type of the file
        filename: Original filename (optional)
        
    Returns:
        Dict with public_url and success status
    """
    if content_type not in ALLOWED_TYPES:
        raise ValueError(f"Invalid file type. Allowed: {', '.join(ALLOWED_TYPES)}")
    
    if len(file_data) > MAX_FILE_SIZE:
        raise ValueError(f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB")
    
    # Generate unique filename
    ext = content_type.split('/')[-1]
    if ext == 'jpeg':
        ext = 'jpg'
    unique_filename = f"{advisor_profile_id}/{uuid.uuid4().hex}.{ext}"
    
    supabase = get_supabase()
    
    # Delete old image if exists
    try:
        existing = supabase.table('advisor_profiles').select('profile_image_url').eq('id', advisor_profile_id).execute()
        if existing.data and existing.data[0].get('profile_image_url'):
            old_url = existing.data[0]['profile_image_url']
            # Extract path from URL
            if ADVISOR_BUCKET in old_url:
                old_path = old_url.split(f'{ADVISOR_BUCKET}/')[-1]
                try:
                    supabase.storage.from_(ADVISOR_BUCKET).remove([old_path])
                except Exception:
                    pass  # Ignore deletion errors
    except Exception as e:
        log_error(f"Error checking existing image: {e}")
    
    # Upload new image
    try:
        result = supabase.storage.from_(ADVISOR_BUCKET).upload(
            path=unique_filename,
            file=file_data,
            file_options={"content-type": content_type, "upsert": "true"}
        )
    except Exception as e:
        error_str = str(e)
        log_error(f"Supabase storage upload error: {error_str}")
        if 'Bucket not found' in error_str:
            raise ValueError(
                "Storage bucket 'advisor-profiles' not found. "
                "Please create it in Supabase Dashboard: Storage > New Bucket > 'advisor-profiles' (public)"
            )
        if '400' in error_str or 'Bad Request' in error_str:
            raise ValueError(
                f"Storage upload failed (400). Check bucket permissions and RLS policies. Error: {error_str}"
            )
        raise ValueError(f"Failed to upload image: {error_str}")
    
    # Get public URL
    public_url = supabase.storage.from_(ADVISOR_BUCKET).get_public_url(unique_filename)
    
    # Update advisor profile with new image URL
    supabase.table('advisor_profiles').update({
        'profile_image_url': public_url,
    }).eq('id', advisor_profile_id).execute()
    
    log_info(f"Uploaded profile image for advisor {advisor_profile_id}")
    
    return {
        'success': True,
        'public_url': public_url,
    }


def upload_advisor_profile_image_base64(
    advisor_profile_id: str,
    base64_data: str,
    content_type: str
) -> Dict[str, Any]:
    """
    Upload an advisor profile image from base64 encoded data.
    
    Args:
        advisor_profile_id: The advisor profile ID
        base64_data: Base64 encoded image data (without data:image/... prefix)
        content_type: MIME type of the file
        
    Returns:
        Dict with public_url and success status
    """
    # Remove data URL prefix if present
    if ',' in base64_data:
        base64_data = base64_data.split(',')[1]
    
    try:
        file_data = base64.b64decode(base64_data)
    except Exception as e:
        raise ValueError(f"Invalid base64 data: {e}")
    
    return upload_advisor_profile_image(advisor_profile_id, file_data, content_type)


def delete_advisor_profile_image(advisor_profile_id: str) -> Dict[str, Any]:
    """
    Delete an advisor's profile image.
    
    Args:
        advisor_profile_id: The advisor profile ID
        
    Returns:
        Dict with success status
    """
    supabase = get_supabase()
    
    # Get current image URL
    result = supabase.table('advisor_profiles').select('profile_image_url').eq('id', advisor_profile_id).execute()
    
    if not result.data or not result.data[0].get('profile_image_url'):
        return {'success': True, 'message': 'No image to delete'}
    
    old_url = result.data[0]['profile_image_url']
    
    # Extract path and delete from storage
    if ADVISOR_BUCKET in old_url:
        old_path = old_url.split(f'{ADVISOR_BUCKET}/')[-1]
        try:
            supabase.storage.from_(ADVISOR_BUCKET).remove([old_path])
        except Exception as e:
            log_error(f"Error deleting image from storage: {e}")
    
    # Clear URL in database
    supabase.table('advisor_profiles').update({
        'profile_image_url': None,
    }).eq('id', advisor_profile_id).execute()
    
    log_info(f"Deleted profile image for advisor {advisor_profile_id}")
    
    return {'success': True}


def get_advisor_profile_image(advisor_profile_id: str) -> Optional[str]:
    """
    Get the profile image URL for an advisor.
    
    Args:
        advisor_profile_id: The advisor profile ID
        
    Returns:
        Public URL or None
    """
    supabase = get_supabase()
    result = supabase.table('advisor_profiles').select('profile_image_url').eq('id', advisor_profile_id).execute()
    
    if result.data and result.data[0].get('profile_image_url'):
        return result.data[0]['profile_image_url']
    return None
