"""Database configuration and Supabase client initialization"""
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Supabase configuration
# These must be set as environment variables - no defaults for security
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "Missing required environment variables: SUPABASE_URL and SUPABASE_ANON_KEY must be set. "
        "Please configure these in your environment or .env file."
    )

# Initialize Supabase client with anon key (for RLS-protected operations)
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Error initializing Supabase client: {e}")
    supabase = None

# Initialize Supabase client with service role key (for admin operations like storage)
supabase_admin: Client = None
if SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as e:
        print(f"Error initializing Supabase admin client: {e}")
        supabase_admin = None

def get_supabase():
    """Get the Supabase client instance (with anon key, respects RLS)"""
    return supabase

def get_supabase_admin():
    """Get the Supabase admin client instance (with service role key, bypasses RLS)
    Use this for server-side operations like storage uploads that need to bypass RLS policies.
    """
    return supabase_admin

