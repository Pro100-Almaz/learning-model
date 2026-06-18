from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GrantThreshold, Specialty, University
from .serializers import GrantCalcResultSerializer, UniversitySerializer
from .services import NoMockError, calculate_grant


class UniversityListView(APIView):
    """GET /careers/universities/ — full catalog with specialties + latest_threshold."""

    permission_classes = [IsAuthenticated]
    serializer_class = UniversitySerializer

    @extend_schema(responses=UniversitySerializer(many=True))
    def get(self, request):
        queryset = University.objects.prefetch_related(
            Prefetch(
                "specialties",
                queryset=Specialty.objects.prefetch_related(
                    Prefetch(
                        "thresholds",
                        queryset=GrantThreshold.objects.all(),
                    )
                ),
            )
        ).order_by("name")

        data = UniversitySerializer(queryset, many=True).data
        return Response(data)


class GrantCalculateView(APIView):
    """POST /careers/calculate/ — predicted score + qualifying grants + goal."""

    permission_classes = [IsAuthenticated]
    serializer_class = GrantCalcResultSerializer

    @extend_schema(request=None, responses=GrantCalcResultSerializer)
    def post(self, request):
        try:
            result = calculate_grant(request.user)
        except NoMockError:
            return Response(
                {
                    "detail": "Сначала пройдите пробный тест по математике.",
                    "code": "no_completed_mock",
                },
                status=status.HTTP_409_CONFLICT,
            )

        payload = GrantCalcResultSerializer(result).data
        return Response(payload)
