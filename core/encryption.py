"""
Iranio — Token encryption at rest using Fernet (SECRET_KEY-derived).
Tokens are never exposed in templates; use get_decrypted_token() only in server-side code.
"""

import base64
import hashlib
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

_FERNET = None


def _get_fernet():
    global _FERNET
    if _FERNET is None:
        try:
            from cryptography.fernet import Fernet
            key = base64.urlsafe_b64encode(
                hashlib.sha256(settings.SECRET_KEY.encode()).digest()
            )
            _FERNET = Fernet(key)
        except Exception as e:
            logger.warning('Fernet init failed, tokens stored plain: %s', e)
            _FERNET = False
    return _FERNET


def encrypt_token(plain: str) -> str:
    if not plain:
        return ''
    f = _get_fernet()
    if not f:
        return plain
    try:
        return f.encrypt(plain.encode()).decode()
    except Exception as e:
        logger.exception('Encrypt failed: %s', e)
        return plain


def decrypt_token(encrypted: str) -> str:
    if not encrypted:
        return ''
    f = _get_fernet()
    if not f:
        return encrypted
    try:
        return f.decrypt(encrypted.encode()).decode()
    except Exception as e:
        logger.debug('Decrypt failed (maybe plain): %s', e)
        return encrypted


def mask_token(token: str, visible: int = 4) -> str:
    """Mask token for UI display. Shows last `visible` chars."""
    if not token or len(token) <= visible:
        return '••••••••'
    return '••••••••' + token[-visible:]


def hash_api_key(plain: str) -> str:
    """One-way hash for API keys. Use verify_api_key for constant-time comparison."""
    if not plain:
        return ''
    from django.contrib.auth.hashers import make_password
    return make_password(plain, hasher='default')


def verify_api_key(plain: str, hashed: str) -> bool:
    """Constant-time verification of API key against stored hash."""
    if not plain or not hashed:
        return False
    from django.contrib.auth.hashers import check_password
    return check_password(plain, hashed)
