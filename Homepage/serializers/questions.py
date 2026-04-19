from rest_framework import serializers

from ..models import FixedQuestion, QuestionAnswer, UnansweredQuestion


class FixedQuestionSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = FixedQuestion
        fields = [
            "id",
            "question",
            "answer",
            "answer_type",
            "overview_description",
            "count",
            "is_active",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "count", "created_by", "created_at", "updated_at"]


class FixedQuestionWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = FixedQuestion
        fields = [
            "question",
            "answer",
            "answer_type",
            "overview_description",
            "is_active",
        ]


class QuestionAnswerSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = QuestionAnswer
        fields = [
            "id",
            "question",
            "answer",
            "answer_type",
            "overview_description",
            "count",
            "is_active",
            "is_fixed",
            "fixed_question",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "count", "created_by", "created_at", "updated_at"]


class QuestionAnswerWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionAnswer
        fields = [
            "question",
            "answer",
            "answer_type",
            "overview_description",
            "is_active",
            "is_fixed",
            "fixed_question",
        ]

    def validate(self, attrs):
        if attrs.get("is_fixed") and not attrs.get("fixed_question"):
            raise serializers.ValidationError(
                {"fixed_question": "Required when is_fixed is true."}
            )
        return attrs


class UnansweredQuestionSerializer(serializers.ModelSerializer):
    assigned_to_username = serializers.CharField(
        source="assigned_to.username", read_only=True
    )

    class Meta:
        model = UnansweredQuestion
        fields = [
            "id",
            "question",
            "status",
            "priority",
            "assigned_to",
            "assigned_to_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class UnansweredQuestionWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnansweredQuestion
        fields = ["question", "status", "priority", "assigned_to"]
