from django.contrib.auth import update_session_auth_hash
from drf_spectacular.utils import extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.views import TokenObtainPairView

from .authentication import APIKeyAuthentication
from .models import APIKey, User
from .serializers import (
    AdminSetPasswordSerializer,
    APIKeyAdminSerializer,
    APIKeySerializer,
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    UserRegistrationSerializer,
    UserSerializer,
)

# Auth accepted on admin/account endpoints: API key, JWT, or session.
ACCOUNT_AUTH = [APIKeyAuthentication, JWTAuthentication, SessionAuthentication]

# UserViewSet actions a non-admin may call on their OWN account.
SELF_SERVICE_ACTIONS = {"me", "change_password", "api_key", "regenerate_api_key"}


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    authentication_classes = ACCOUNT_AUTH
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering_fields = ["username", "email", "date_joined"]
    ordering = ["username"]

    def get_permissions(self):
        """Self-service actions need only authentication; everything else
        (list/create/update/delete users, key & password admin) is admin-only."""
        if self.action in SELF_SERVICE_ACTIONS:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAdminUser()]

    def get_serializer_class(self):
        if self.action == "create":
            return UserRegistrationSerializer
        return UserSerializer

    @extend_schema(responses={201: UserSerializer})
    def create(self, request, *args, **kwargs):
        """Admin only: create a user and issue an API key."""
        serializer = UserRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        api_key, _ = APIKey.objects.get_or_create(user=user)
        return Response(
            {**UserSerializer(user).data, "api_key": api_key.key},
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=UserRegistrationSerializer,
        responses={201: UserSerializer},
        description="Admin only: Create a new user account with API key.",
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.IsAdminUser],
        url_path="create",
    )
    def create_user(self, request):
        """Admin only: Create new user and generate API key."""
        serializer = UserRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        api_key, _ = APIKey.objects.get_or_create(user=user)
        response_data = {
            **UserSerializer(user).data,
            "api_key": api_key.key,
        }
        return Response(response_data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=UserRegistrationSerializer,
        responses={201: UserSerializer},
        description="Admin only: register a new user (alias of create) with an API key.",
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.IsAdminUser],
        url_path="register",
    )
    def register(self, request):
        """Admin only: register (create) a new user and generate an API key."""
        return self.create(request)

    @extend_schema(responses={200: UserSerializer})
    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(
        request=ChangePasswordSerializer,
        responses={200: None},
        description="Change your own password (requires current password).",
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="change-password",
    )
    def change_password(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["old_password"]):
            return Response(
                {"old_password": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        # Keep the current session valid after the password change.
        update_session_auth_hash(request, user)
        return Response({"detail": "Password updated successfully."})

    @extend_schema(
        request=AdminSetPasswordSerializer,
        responses={200: None},
        description="Admin only: set another user's password (no old password needed).",
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[permissions.IsAdminUser],
        url_path="set-password",
    )
    def set_password(self, request, pk=None):
        target = self.get_object()
        serializer = AdminSetPasswordSerializer(data=request.data, context={"target_user": target})
        serializer.is_valid(raise_exception=True)
        target.set_password(serializer.validated_data["new_password"])
        target.save(update_fields=["password"])
        return Response({"detail": f"Password updated for {target.username}."})

    @extend_schema(responses={200: APIKeySerializer})
    @action(
        detail=False,
        methods=["get"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="api-key",
    )
    def api_key(self, request):
        """Get or create the current user's API key."""
        api_key, _ = APIKey.objects.get_or_create(user=request.user)
        return Response(APIKeySerializer(api_key).data)

    @extend_schema(responses={200: APIKeySerializer})
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="regenerate-api-key",
    )
    def regenerate_api_key(self, request):
        """Generate a new API key for the current user."""
        api_key, _ = APIKey.objects.get_or_create(user=request.user)
        api_key.key = APIKey.generate_key()
        api_key.save()
        return Response(APIKeySerializer(api_key).data)

    @extend_schema(responses={200: APIKeySerializer})
    @action(
        detail=True,
        methods=["get"],
        permission_classes=[permissions.IsAdminUser],
        url_path="api-key",
    )
    def get_user_api_key(self, request, pk=None):
        """Admin only: Get a user's API key."""
        user = self.get_object()
        api_key, _ = APIKey.objects.get_or_create(user=user)
        return Response(APIKeySerializer(api_key).data)

    @extend_schema(responses={200: APIKeySerializer})
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[permissions.IsAdminUser],
        url_path="regenerate-api-key",
    )
    def admin_regenerate_api_key(self, request, pk=None):
        """Admin only: Regenerate a user's API key."""
        user = self.get_object()
        api_key, _ = APIKey.objects.get_or_create(user=user)
        api_key.key = APIKey.generate_key()
        api_key.save()
        return Response(APIKeySerializer(api_key).data)


class APIKeyViewSet(viewsets.ModelViewSet):
    """Admin-only control plane for API keys across all users.

    list/retrieve/create/delete plus actions to revoke, activate, and
    regenerate (rotate) any user's key.
    """

    queryset = APIKey.objects.select_related("user").all()
    serializer_class = APIKeyAdminSerializer
    authentication_classes = ACCOUNT_AUTH
    permission_classes = [permissions.IsAdminUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["user__username", "user__email"]
    ordering_fields = ["created_at", "last_used_at", "is_active"]
    ordering = ["-created_at"]

    @extend_schema(responses={200: APIKeyAdminSerializer})
    @action(detail=True, methods=["post"], url_path="regenerate")
    def regenerate(self, request, pk=None):
        """Rotate the key (old key stops working immediately)."""
        api_key = self.get_object()
        api_key.key = APIKey.generate_key()
        api_key.save(update_fields=["key", "updated_at"])
        return Response(APIKeyAdminSerializer(api_key).data)

    @extend_schema(responses={200: APIKeyAdminSerializer})
    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        """Disable the key without deleting it."""
        api_key = self.get_object()
        api_key.is_active = False
        api_key.save(update_fields=["is_active", "updated_at"])
        return Response(APIKeyAdminSerializer(api_key).data)

    @extend_schema(responses={200: APIKeyAdminSerializer})
    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        """Re-enable a revoked key."""
        api_key = self.get_object()
        api_key.is_active = True
        api_key.save(update_fields=["is_active", "updated_at"])
        return Response(APIKeyAdminSerializer(api_key).data)
