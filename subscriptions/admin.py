from django.contrib import admin

from .models import Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "price_usd",
        "monthly_questions",
        "monthly_token_cap",
        "max_documents",
        "llm_model",
        "is_active",
        "sort_order",
    )
    list_filter = ("is_active", "llm_model")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "plan",
        "status",
        "current_period_start",
        "current_period_end",
        "auto_renew",
    )
    list_filter = ("status", "plan", "auto_renew")
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
    readonly_fields = ("id", "created_at", "updated_at")
