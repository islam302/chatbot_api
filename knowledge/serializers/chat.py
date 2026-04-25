from rest_framework import serializers

from ..models import AnswerSource, ChatFeedback


class ChatMessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["user", "assistant", "system"])
    content = serializers.CharField()


class ChatRequestSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=2000)
    history = ChatMessageSerializer(many=True, required=False, default=list)
    language = serializers.CharField(max_length=8, default="ar")


class ChatResponseSerializer(serializers.Serializer):
    answer = serializers.CharField()
    source = serializers.ChoiceField(choices=AnswerSource.choices)
    source_id = serializers.CharField(allow_blank=True, required=False)
    sources = serializers.ListField(child=serializers.DictField(), required=False)
    confident = serializers.BooleanField(default=True)
    response_time_ms = serializers.IntegerField(required=False)


class QuestionSearchSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=512)
    question_type = serializers.ChoiceField(
        choices=["fixed", "dynamic", "unanswered", "all"], default="all"
    )
    language = serializers.CharField(max_length=8, required=False, allow_blank=True)
    limit = serializers.IntegerField(default=20, min_value=1, max_value=200)


class BulkQuestionUpdateSerializer(serializers.Serializer):
    ACTIONS = ["activate", "deactivate", "delete"]

    question_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    action = serializers.ChoiceField(choices=ACTIONS)


class AnalyticsSerializer(serializers.Serializer):
    total_fixed_questions = serializers.IntegerField()
    total_dynamic_questions = serializers.IntegerField()
    total_unanswered = serializers.IntegerField()
    total_documents = serializers.IntegerField()
    total_chunks = serializers.IntegerField()
    most_asked_questions = serializers.ListField(child=serializers.DictField())
    language_distribution = serializers.DictField(child=serializers.IntegerField())
    unanswered_by_status = serializers.DictField(child=serializers.IntegerField())
    feedback_summary = serializers.DictField(child=serializers.IntegerField())


class ChatFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatFeedback
        fields = [
            "id",
            "question",
            "answer",
            "source",
            "source_id",
            "rating",
            "comment",
            "user",
            "created_at",
        ]
        read_only_fields = ["id", "user", "created_at"]
