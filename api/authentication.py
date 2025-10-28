import jwt
from ninja.security import HttpBearer
from django.contrib.auth.models import User
from django.http import HttpRequest
from .jwt_utils import decode_jwt_token


class JWTAuth(HttpBearer):
    """
    Custom JWT authentication for Django Ninja.
    Validates Bearer tokens from the Authorization header.
    """
    
    def authenticate(self, request: HttpRequest, token: str):
        """
        Authenticate the request using JWT token.
        
        Args:
            request: Django HttpRequest object
            token: JWT token string from Bearer header
            
        Returns:
            User object if authentication successful, None otherwise
        """
        try:
            # Decode and validate the token
            payload = decode_jwt_token(token)
            
            # Get user from payload
            user_id = payload.get('user_id')
            
            if not user_id:
                return None
            
            # Fetch user from database
            try:
                user = User.objects.get(pk=user_id, is_active=True)
                return user
            except User.DoesNotExist:
                return None
                
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None
        except Exception:
            return None

