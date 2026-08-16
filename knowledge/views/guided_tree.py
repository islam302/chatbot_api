"""Guided question tree API — cached multilingual reads + canonical-only writes.

Reads (public to any authenticated tenant): pick a language → get the nested
tree (cached). Writes (owner/admin): always applied to the canonical language;
mirrors regenerate in the background.
"""

import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet
from rest_framework_simplejwt.authentication import JWTAuthentication

from Authentication.authentication import APIKeyAuthentication
from ..models import AvailableLanguage, QuestionTreeNode
from ..serializers import (
    AvailableLanguageSerializer,
    QuestionTreeNodeSerializer,
    QuestionTreeNodeUpdateSerializer,
    QuestionTreeNodeWriteSerializer,
)
from ..services import guided_tree as gt

logger = logging.getLogger(__name__)

AUTH = [APIKeyAuthentication, JWTAuthentication]


class GuidedTreeViewSet(ViewSet):
    """Multilingual guided question tree, scoped to the caller (tenant)."""

    authentication_classes = AUTH
    permission_classes = [permissions.IsAuthenticated]

    def _owned(self, request, pk):
        return QuestionTreeNode.objects.filter(owner=request.user, pk=pk).first()

    def _feature_blocked(self, request):
        """None if allowed to author the tree, else a 402 Response."""
        try:
            from subscriptions.features import has_feature

            allowed = has_feature(request.user, "guided_tree")
        except Exception:
            allowed = True  # fail open on a billing-layer hiccup
        if allowed:
            return None
        return Response(
            {"detail": "The guided question tree isn't enabled for your account."},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )

    # -- Reads ------------------------------------------------------------- #
    @extend_schema(
        parameters=[OpenApiParameter("language", str, description="Language code (default: canonical)")],
        responses={200: dict},
    )
    def list(self, request):
        """Nested tree for a language (cached). Defaults to the canonical language."""
        language = request.query_params.get("language") or gt.canonical_language()
        return Response({
            "language": language,
            "canonical_language": gt.canonical_language(),
            "tree": gt.get_tree(request.user, language),
        })

    @extend_schema(request=QuestionTreeNodeWriteSerializer, responses={201: QuestionTreeNodeSerializer})
    def create(self, request):
        """Add a node (applied to the canonical language; mirrors follow async)."""
        blocked = self._feature_blocked(request)
        if blocked is not None:
            return blocked
        ser = QuestionTreeNodeWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        parent = data.get("parent")
        if parent is not None and parent.owner_id != request.user.id:
            return Response({"detail": "Unknown parent."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            node = gt.create_node(
                owner=request.user,
                parent=parent,
                title=data["title"],
                answer=data.get("answer"),
                order=data.get("order"),
                is_active=data.get("is_active", True),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(QuestionTreeNodeSerializer(node).data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        node = self._owned(request, pk)
        if node is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(QuestionTreeNodeSerializer(node).data)

    @extend_schema(request=QuestionTreeNodeUpdateSerializer, responses={200: QuestionTreeNodeSerializer})
    def partial_update(self, request, pk=None):
        node = self._owned(request, pk)
        if node is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        ser = QuestionTreeNodeUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        try:
            updated = gt.update_node(node, **ser.validated_data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(QuestionTreeNodeSerializer(updated).data)

    def destroy(self, request, pk=None):
        node = self._owned(request, pk)
        if node is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        gt.delete_node(node)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # -- Extra actions ----------------------------------------------------- #
    @extend_schema(
        parameters=[OpenApiParameter("language", str, description="Language code (default: canonical)")],
        responses={200: QuestionTreeNodeSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="flat")
    def flat(self, request):
        """Flat dump of every node in a language (admin/debug)."""
        language = request.query_params.get("language") or gt.canonical_language()
        qs = QuestionTreeNode.objects.filter(owner=request.user, language=language).order_by(
            "parent_id", "order", "created_at"
        )
        return Response(QuestionTreeNodeSerializer(qs, many=True).data)

    @extend_schema(responses={202: dict})
    @action(detail=False, methods=["post"], url_path="retranslate")
    def retranslate(self, request):
        """Rebuild one (or all) non-canonical language(s) from the canonical tree."""
        language = request.data.get("language")
        targets = [language] if language else gt.active_target_languages()
        for lang in targets:
            if gt.is_canonical(lang):
                continue
            gt._dispatch(gt.resync_language, request.user, lang)
        return Response({"status": "retranslating", "languages": targets},
                        status=status.HTTP_202_ACCEPTED)


class AvailableLanguageViewSet(ViewSet):
    """Manage the languages the tree is offered in (admin writes; anyone reads)."""

    authentication_classes = AUTH
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        active_only = request.query_params.get("active") in ("1", "true", "yes")
        qs = AvailableLanguage.objects.all()
        if active_only:
            qs = qs.filter(is_active=True)
        return Response(AvailableLanguageSerializer(qs.order_by("name"), many=True).data)

    def _require_staff(self, request):
        return request.user.is_staff or request.user.is_superuser

    @extend_schema(request=AvailableLanguageSerializer, responses={201: AvailableLanguageSerializer})
    def create(self, request):
        """Add a language and generate its mirror of the caller's canonical tree."""
        if not self._require_staff(request):
            return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)
        ser = AvailableLanguageSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if ser.validated_data["code"] == gt.canonical_language():
            return Response(
                {"detail": "That is the canonical language; it already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lang, _ = AvailableLanguage.objects.update_or_create(
            code=ser.validated_data["code"],
            defaults={"name": ser.validated_data["name"],
                      "is_active": ser.validated_data.get("is_active", True)},
        )
        # Generate this language's whole tree from the caller's canonical tree.
        gt._dispatch(gt.resync_language, request.user, lang.code)
        return Response(AvailableLanguageSerializer(lang).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        """Remove a language (never the canonical one)."""
        if not self._require_staff(request):
            return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)
        if pk == gt.canonical_language():
            return Response(
                {"detail": "The canonical language cannot be removed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lang = AvailableLanguage.objects.filter(code=pk).first()
        if lang is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # AvailableLanguage is global, so removing it drops that language's mirror
        # nodes for EVERY tenant (leaving them would orphan untranslatable content).
        owner_ids = list(
            QuestionTreeNode.objects.filter(language=pk)
            .values_list("owner_id", flat=True).distinct()
        )
        QuestionTreeNode.objects.filter(language=pk).delete()
        lang.delete()
        for owner_id in owner_ids:
            gt.invalidate_cache(owner_id, [pk])
        return Response(status=status.HTTP_204_NO_CONTENT)
