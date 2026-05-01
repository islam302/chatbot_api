from drf_spectacular.utils import extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from ..models import APIKey, User
from ..serializers import (
    APIKeySerializer,
    CustomTokenObtainPairSerializer,
    UserRegistrationSerializer,
    UserSerializer,
)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering_fields = ["username", "email", "date_joined"]
    ordering = ["username"]

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
        # Automatically create API key for new user
        api_key, _ = APIKey.objects.get_or_create(user=user)
        response_data = {
            **UserSerializer(user).data,
            "api_key": api_key.key,
        }
        return Response(response_data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: UserSerializer})
    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(responses={200: APIKeySerializer})
    @action(
        detail=False,
        methods=["get"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="api-key",
    )
    def api_key(self, request):
        """Get or create user's API key."""
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
        """Generate a new API key for the user."""
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
