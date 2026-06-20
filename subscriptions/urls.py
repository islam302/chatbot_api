from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import MySubscriptionView, PlanViewSet, SubscriptionViewSet

router = DefaultRouter()
router.register("plans", PlanViewSet, basename="plan")
router.register("subscriptions", SubscriptionViewSet, basename="subscription")

urlpatterns = [
    path("my-subscription/", MySubscriptionView.as_view(), name="my-subscription"),
    path("", include(router.urls)),
]
