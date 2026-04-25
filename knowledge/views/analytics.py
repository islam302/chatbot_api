from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import (
    FixedQuestion,
    QuestionAnswer,
    SimpleQuestionTree,
    UnansweredQuestion,
    UploadedDocument,
)
from ..serializers import AnalyticsSerializer


class AnalyticsAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: AnalyticsSerializer})
    def get(self, request):
        most_asked = list(
            FixedQuestion.objects.order_by("-count")[:10].values("id", "question", "count")
        )
        for item in most_asked:
            item["id"] = str(item["id"])

        language_distribution = dict(
            SimpleQuestionTree.objects.values_list("language")
            .annotate(c=Count("id"))
            .values_list("language", "c")
        )

        unanswered_by_status = dict(
            UnansweredQuestion.objects.values_list("status")
            .annotate(c=Count("id"))
            .values_list("status", "c")
        )

        payload = {
            "total_fixed_questions": FixedQuestion.objects.count(),
            "total_dynamic_questions": QuestionAnswer.objects.count(),
            "total_unanswered": UnansweredQuestion.objects.count(),
            "total_documents": UploadedDocument.objects.count(),
            "most_asked_questions": most_asked,
            "language_distribution": language_distribution,
            "unanswered_by_status": unanswered_by_status,
        }
        return Response(AnalyticsSerializer(payload).data)
