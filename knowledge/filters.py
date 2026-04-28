from django_filters import rest_framework as filters

from .models import QuestionAnswer, SimpleQuestionTree, UploadedDocument


class QuestionAnswerFilter(filters.FilterSet):
    min_count = filters.NumberFilter(field_name="count", lookup_expr="gte")

    class Meta:
        model = QuestionAnswer
        fields = ["answer_type", "is_active", "created_by"]


class SimpleQuestionTreeFilter(filters.FilterSet):
    root_only = filters.BooleanFilter(method="filter_root_only")

    class Meta:
        model = SimpleQuestionTree
        fields = ["language", "is_active", "parent", "created_by"]

    def filter_root_only(self, queryset, name, value):
        if value:
            return queryset.filter(parent__isnull=True)
        return queryset


class UploadedDocumentFilter(filters.FilterSet):
    class Meta:
        model = UploadedDocument
        fields = ["processing_status", "is_active", "uploaded_by"]
