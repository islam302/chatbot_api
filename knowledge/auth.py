import logging
from datetime import datetime

from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import APIKey

logger = logging.getLogger(__name__)


class APIKeyAuthentication(TokenAuthentication):
    """Authenticate using per-user API key in Authorization header."""

    keyword = "ApiKey"

    def authenticate_credentials(self, key):
        """Validate API key and return (user, auth)."""
        try:
            api_key = APIKey.objects.select_related("user").get(key=key)
        except APIKey.DoesNotExist:
            raise AuthenticationFailed("Invalid API key.")

        if not api_key.is_active:
            raise AuthenticationFailed("API key is inactive.")

        if not api_key.user.is_active:
            raise AuthenticationFailed("User account is inactive.")

        # Update last used timestamp
        api_key.last_used_at = datetime.now()
        api_key.save(update_fields=["last_used_at"])

        return (api_key.user, api_key)
