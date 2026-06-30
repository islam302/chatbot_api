from rest_framework import serializers

from ..models import UnansweredQuestion


class UnansweredQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnansweredQuestion
        fields = [
            "id",
            "question",
            "language",
            "reason",
            "status",          # the only writable field (review workflow)
            "occurrences",
            "last_asked_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "question",
            "language",
            "reason",
            "occurrences",
            "last_asked_at",
            "created_at",
        ]
