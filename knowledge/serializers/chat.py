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
