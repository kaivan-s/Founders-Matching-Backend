"""Input validation and sanitization utilities"""
import re
from typing import Any, Dict, List, Optional
from flask import request


def sanitize_string(value: Any, max_length: Optional[int] = None, allow_empty: bool = True) -> Optional[str]:
    """Sanitize string input"""
    if value is None:
        return None if allow_empty else ""
    
    # Convert to string and strip whitespace
    sanitized = str(value).strip()
    
    # Remove null bytes and control characters (except newlines and tabs)
    sanitized = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f]', '', sanitized)
    
    # Enforce max length
    if max_length and len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    
    return sanitized if (sanitized or allow_empty) else None


def validate_email(email: str) -> bool:
    """Validate email format"""
    if not email:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def validate_url(url: str) -> bool:
    """Validate URL format"""
    if not url:
        return False
    pattern = r'^https?://[^\s/$.?#].[^\s]*$'
    return bool(re.match(pattern, url))


def sanitize_list(value: Any, max_items: Optional[int] = None) -> List[str]:
    """Sanitize list input"""
    if not value:
        return []
    
    if not isinstance(value, list):
        return []
    
    sanitized = [sanitize_string(item) for item in value if item]
    
    if max_items and len(sanitized) > max_items:
        sanitized = sanitized[:max_items]
    
    return sanitized


def validate_integer(value: Any, min_value: Optional[int] = None, max_value: Optional[int] = None) -> Optional[int]:
    """Validate and convert to integer"""
    if value is None:
        return None
    
    try:
        int_value = int(value)
        if min_value is not None and int_value < min_value:
            return min_value
        if max_value is not None and int_value > max_value:
            return max_value
        return int_value
    except (ValueError, TypeError):
        return None


def validate_enum(value: Any, allowed_values: List[str], case_sensitive: bool = True) -> Optional[str]:
    """Validate value is in allowed enum values"""
    if not value:
        return None
    
    str_value = str(value).strip()
    
    if not case_sensitive:
        str_value = str_value.upper()
        allowed_values = [v.upper() for v in allowed_values]
    
    return str_value if str_value in allowed_values else None


def sanitize_json_input(data: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize JSON input based on schema
    
    Schema format:
    {
        'field_name': {
            'type': 'string' | 'int' | 'list' | 'email' | 'url' | 'enum',
            'required': bool,
            'max_length': int (for strings),
            'max_items': int (for lists),
            'allowed_values': List[str] (for enum),
            'min': int, 'max': int (for integers),
            'default': any
        }
    }
    """
    sanitized = {}
    
    for field_name, field_schema in schema.items():
        field_type = field_schema.get('type', 'string')
        required = field_schema.get('required', False)
        default = field_schema.get('default')
        
        value = data.get(field_name, default)
        
        # Check required fields
        if required and (value is None or value == ''):
            raise ValueError(f"Field '{field_name}' is required")
        
        # Skip None values unless required
        if value is None:
            continue
        
        # Validate and sanitize based on type
        if field_type == 'string':
            max_length = field_schema.get('max_length')
            sanitized[field_name] = sanitize_string(value, max_length=max_length)
        
        elif field_type == 'int':
            min_val = field_schema.get('min')
            max_val = field_schema.get('max')
            sanitized[field_name] = validate_integer(value, min_value=min_val, max_value=max_val)
        
        elif field_type == 'list':
            max_items = field_schema.get('max_items')
            sanitized[field_name] = sanitize_list(value, max_items=max_items)
        
        elif field_type == 'email':
            email = sanitize_string(value)
            if email and not validate_email(email):
                raise ValueError(f"Invalid email format for field '{field_name}'")
            sanitized[field_name] = email
        
        elif field_type == 'url':
            url = sanitize_string(value)
            if url and not validate_url(url):
                raise ValueError(f"Invalid URL format for field '{field_name}'")
            sanitized[field_name] = url
        
        elif field_type == 'enum':
            allowed_values = field_schema.get('allowed_values', [])
            enum_value = validate_enum(value, allowed_values)
            if enum_value is None:
                raise ValueError(f"Field '{field_name}' must be one of: {', '.join(allowed_values)}")
            sanitized[field_name] = enum_value
    
    return sanitized


def validate_query_params(params: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and sanitize query parameters"""
    return sanitize_json_input(params, schema)

