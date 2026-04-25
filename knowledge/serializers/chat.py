from rest_framework import serializers


class ChatMessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["user", "assistant", "system"])
    content = serializers.CharField()


class ChatRequestSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=2000)
    history = ChatMessageSerializer(many=True, required=False, default=list)
    chat_type = serializers.ChoiceField(
        choices=["rag", "questions"], default="rag"
    )
    language = serializers.CharField(max_length=8, default="ar")


class ChatSourceSerializer(serializers.Serializer):
    document_id = serializers.UUIDField(required=False)
    filename = serializers.CharField(required=False)
    score = serializers.FloatField(required=False)


class ChatResponseSerializer(serializers.Serializer):
    answer = serializers.CharField()
    sources = ChatSourceSerializer(many=True, required=False)
    response_time_ms = serializers.IntegerField(required=False)
    chat_type = serializers.CharField(required=False)


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
    most_asked_questions = serializers.ListField(child=serializers.DictField())
    language_distribution = serializers.DictField(child=serializers.IntegerField())
    unanswered_by_status = serializers.DictField(child=serializers.IntegerField())
