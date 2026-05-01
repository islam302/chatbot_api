import logging

import requests
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from ..auth import APIKeyAuthentication
from ..models import DocumentStatus, UploadedDocument
from ..services.api_content_processor import APIContentRAGProcessor, APIContentProcessingError

logger = logging.getLogger(__name__)


class SyncAPIContentView(APIView):
    """Ingest content from external API into RAG system."""

    authentication_classes = [APIKeyAuthentication, TokenAuthentication]
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
        api_url = request.data.get("api_url")
        document_name = request.data.get("document_name", "API Content")
        items_key = request.data.get("items_key", "results")

        if not api_url:
            return Response(
                {"detail": "api_url is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            processor = APIContentRAGProcessor(
                document_name=document_name,
                user=request.user,
                api_url=api_url,
                items_key=items_key,
            )
            stats = processor.process_items(items)

            return Response(
                {
                    "status": "success",
                    "processed": stats["processed"],
                    "chunks_created": stats["chunks_created"],
                    "errors": stats["errors"],
                },
                status=status.HTTP_200_OK,
            )

        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            return Response(
                {"detail": f"API request failed: {e}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except APIContentProcessingError as e:
            logger.error(f"Processing failed: {e}")
            return Response(
                {"detail": f"Processing failed: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return Response(
                {"detail": f"Unexpected error: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
