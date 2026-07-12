"""Analytics endpoints: per-tag stats + weak-tag recommendations."""

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics.serializers import RecommendationSerializer, TagStatSerializer
from apps.analytics.services import compute_recommendations, compute_tag_stats


class TagStatsView(APIView):
    """GET /api/v1/analytics/tags/ — per-tag performance."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=TagStatSerializer(many=True))
    def get(self, request):
        data = compute_tag_stats(request.user)
        return Response(data)


class RecommendationsView(APIView):
    """GET /api/v1/analytics/recommendations/ — weak tags + lessons."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=RecommendationSerializer(many=True))
    def get(self, request):
        data = compute_recommendations(request.user)
        return Response(data)
