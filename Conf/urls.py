from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import FileResponse, Http404, JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


def health(_request):
    return JsonResponse({"status": "ok"})


def api_tester(_request):
    """Serve the interactive API test console (same-origin → no CORS issues)."""
    html = settings.BASE_DIR / "api-tester.html"
    if not html.exists():
        raise Http404("api-tester.html not found")
    return FileResponse(open(html, "rb"), content_type="text/html")


def paddle_test(_request):
    """Serve the Paddle checkout test page (needs a real origin, not file://)."""
    html = settings.BASE_DIR / "paddle-checkout-test.html"
    if not html.exists():
        raise Http404("paddle-checkout-test.html not found")
    return FileResponse(open(html, "rb"), content_type="text/html")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("api-tester/", api_tester, name="api-tester"),
    path("paddle-test/", paddle_test, name="paddle-test"),
    # OpenAPI schema and docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # API v1
    path("api/v1/", include("Authentication.urls")),
    path("api/v1/", include("knowledge.urls")),
    path("api/v1/", include("subscriptions.urls")),
    path("api/v1/whatsapp/", include("WhatsApp.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
