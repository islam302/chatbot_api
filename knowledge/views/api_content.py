import logging

import requests
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from Authentication.authentication import APIKeyAuthentication
from ..models import DocumentStatus, UploadedDocument
from ..services.api_content_processor import APIContentRAGProcessor, APIContentProcessingError
from ..services.net import UnsafeURLError, validate_public_url

logger = logging.getLogger(__name__)


class SyncAPIContentView(APIView):
    """Ingest content from external API into RAG system."""

    authentication_classes = [APIKeyAuthentication, JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request={
            "type": "object",
            "properties": {
                "api_url": {"type": "string", "description": "API endpoint URL"},
                "document_name": {
                    "type": "string",
                    "description": "Virtual document name (default: 'API Content')",
                },
                "items_key": {
                    "type": "string",
                    "description": "JSON key containing items list (default: 'results')",
                },
            },
            "required": ["api_url"],
        },
        responses={
            200: {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "processed": {"type": "integer"},
                    "chunks_created": {"type": "integer"},
                    "errors": {"type": "integer"},
                },
            },
        },
    )
    def post(self, request):
        """Fetch content from API and process for RAG."""
        # Feature gate: importing knowledge from an external API is not on the
        # free tier — upgrade required.
        try:
            from subscriptions.services import can_sync_api_content

            allowed = can_sync_api_content(request.user)
        except Exception:
            allowed = True  # fail open: never block on a billing-layer hiccup
        if not allowed:
            return Response(
                {
                    "detail": "Importing content from an external API isn't available "
                    "on the free plan. Upgrade to enable it."
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        api_url = request.data.get("api_url")
        document_name = request.data.get("document_name", "API Content")
        items_key = request.data.get("items_key", "results")

        if not api_url:
            return Response(
                {"detail": "api_url is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # SSRF guard: reject internal/reserved destinations before connecting.
        try:
            validate_public_url(api_url)
        except UnsafeURLError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Fetch from API
            logger.info(f"Fetching from {api_url}")
            response = requests.get(api_url, timeout=30)
            response.raise_for_status()

            data = response.json()
            items = data.get(items_key, [])

            if not items:
                return Response(
                    {"detail": f"No items found under key '{items_key}'"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            logger.info(f"Fetched {len(items)} items from API")

            # Process for RAG
            full_refresh = str(request.data.get("full_refresh", "")).lower() in {
                "1", "true", "yes", "on"
            }

            processor = APIContentRAGProcessor(
                document_name=document_name,
                user=request.user,
                api_url=api_url,
                items_key=items_key,
            )
            stats = processor.process_items(items, full_refresh=full_refresh)

            return Response(
                {"status": "success", **stats},
                status=status.HTTP_200_OK,
            )

        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            return Response(
                {"detail": "Could not fetch content from the provided URL."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except APIContentProcessingError as e:
            logger.error(f"Processing failed: {e}")
            return Response(
                {"detail": "Failed to process the fetched content."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception:
            logger.exception("Unexpected error while syncing API content")
            return Response(
                {"detail": "An unexpected error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
