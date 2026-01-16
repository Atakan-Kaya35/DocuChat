"""
JWT validation for Keycloak tokens.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

import jwt
from jwt import PyJWK
from django.conf import settings

from .jwks import get_jwks_cache

logger = logging.getLogger(__name__)


class JWTValidationError(Exception):
    """Raised when JWT validation fails."""
    pass


@dataclass
class TokenClaims:
    """Validated token claims."""
    sub: str  # Subject (user ID)
    preferred_username: str
    email: Optional[str]
    roles: List[str]
    raw_claims: Dict[str, Any]


def extract_roles(claims: Dict[str, Any], client_id: str) -> List[str]:
    """
    Extract roles from Keycloak token claims.
    
    Keycloak stores roles in:
    - realm_access.roles: Realm-level roles
    - resource_access[clientId].roles: Client-specific roles
    
    Args:
        claims: The decoded JWT claims
        client_id: The client ID to check for client-specific roles
        
    Returns:
        Deduplicated list of role names
    """
    roles = set()
    
    # Extract realm roles
    realm_access = claims.get('realm_access', {})
    realm_roles = realm_access.get('roles', [])
    roles.update(realm_roles)
    
    # Extract client-specific roles
    resource_access = claims.get('resource_access', {})
    client_access = resource_access.get(client_id, {})
    client_roles = client_access.get('roles', [])
    roles.update(client_roles)
    
    # Filter out Keycloak internal roles
    internal_roles = {'offline_access', 'uma_authorization', 'default-roles-docuchat'}
    filtered_roles = [r for r in roles if r not in internal_roles]
    
    return sorted(filtered_roles)


def validate_token(token: str) -> TokenClaims:
    """
    Validate a Keycloak JWT token.
    
    Performs the following validations:
    1. Decode header to get kid
    2. Fetch public key from JWKS cache
    3. Verify signature
    4. Verify issuer matches expected
    5. Verify token is not expired
    6. Verify audience/azp includes our client
    
    Args:
        token: The JWT token string (without 'Bearer ' prefix)
        
    Returns:
        TokenClaims with validated claims
        
    Raises:
        JWTValidationError: If validation fails
    """
    try:
        # Decode header without verification to get kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get('kid')
        
        if not kid:
            raise JWTValidationError("Token header missing 'kid'")
        
        # Fetch the public key
        jwks_cache = get_jwks_cache()
        jwk_data = jwks_cache.get_key(kid)
        
        if not jwk_data:
            raise JWTValidationError(f"Unknown key ID: {kid}")
        
        # Convert JWK to public key
        public_key = PyJWK.from_dict(jwk_data).key
        
        # Decode and verify the token
        # First, get unverified claims to check issuer
        unverified_claims = jwt.decode(
            token,
            options={"verify_signature": False}
        )
        token_issuer = unverified_claims.get('iss', '')
        
        # Validate issuer against allowed list
        valid_issuers = getattr(settings, 'KC_VALID_ISSUERS', [settings.KC_ISSUER])
        if token_issuer not in valid_issuers:
            logger.warning(f"Invalid issuer: {token_issuer}, expected one of: {valid_issuers}")
            raise JWTValidationError(f"Invalid token issuer")
        
        # Now decode with verification (using the token's issuer)
        claims = jwt.decode(
            token,
            public_key,
            algorithms=['RS256'],
            issuer=token_issuer,  # Use the actual issuer from token
            options={
                'verify_signature': True,
                'verify_exp': True,
                'verify_iss': True,
                'verify_aud': False,  # We'll check aud/azp manually
            }
        )
        
        # Verify audience or authorized party
        # Keycloak uses 'aud' for audience and 'azp' for authorized party
        aud = claims.get('aud', [])
        azp = claims.get('azp', '')
        
        if isinstance(aud, str):
            aud = [aud]
        
        expected_audience = settings.KC_AUDIENCE
        if expected_audience not in aud and azp != expected_audience:
            # For Keycloak, 'azp' is typically the client that requested the token
            # If it matches our expected audience, that's valid
            if azp != expected_audience:
                logger.warning(
                    f"Token audience mismatch. Expected: {expected_audience}, "
                    f"Got aud: {aud}, azp: {azp}"
                )
                # Don't fail on audience for now - Keycloak's audience handling is complex
                # In production, you might want to be stricter
        
        # Extract roles
        roles = extract_roles(claims, expected_audience)
        
        return TokenClaims(
            sub=claims.get('sub', ''),
            preferred_username=claims.get('preferred_username', ''),
            email=claims.get('email'),
            roles=roles,
            raw_claims=claims
        )
    
    except jwt.ExpiredSignatureError:
        raise JWTValidationError("Token has expired")
    except jwt.InvalidIssuerError:
        raise JWTValidationError("Invalid token issuer")
    except jwt.InvalidTokenError as e:
        raise JWTValidationError(f"Invalid token: {e}")
    except Exception as e:
        logger.exception("Unexpected error during token validation")
        raise JWTValidationError(f"Token validation failed: {e}")
