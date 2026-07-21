from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    MySubscriptionView,
    PaddleWebhookView,
    PlanViewSet,
    SubscriptionViewSet,
)

router = DefaultRouter()
router.register("plans", PlanViewSet, basename="plan")
router.register("subscriptions", SubscriptionViewSet, basename="subscription")

urlpatterns = [
    path("my-subscription/", MySubscriptionView.as_view(), name="my-subscription"),
    # Public: Paddle Billing notifications (payment fulfillment).
    path("billing/paddle/webhook/", PaddleWebhookView.as_view(), name="paddle-webhook"),
    path("", include(router.urls)),
]
