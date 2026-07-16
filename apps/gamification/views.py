"""Gamification views — only GET /gamification/me/ per contract."""
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.gamification import services
from apps.gamification.serializers import GamificationSerializer


class GamificationMeView(APIView):
    """Return XP, level, and streak details for the current student."""

    permission_classes = [IsAuthenticated]
    serializer_class = GamificationSerializer

    @extend_schema(responses=GamificationSerializer)
    def get(self, request):
        user = request.user
        progress = services.get_or_create_progress(user)
        streak = services.get_or_create_streak(user)

        code, label = services.level_for_xp(progress.total_xp)
        # Persist a corrected level_code lazily if it drifted (e.g. levels changed).
        if progress.level_code != code:
            progress.level_code = code
            progress.save(update_fields=["level_code"])

        today = timezone.localdate()
        payload = {
            "total_xp": progress.total_xp,
            "level_code": code,
            "level_label": label,
            "xp_to_next_level": services.compute_xp_to_next_level(progress),
            "streak": {
                "current": streak.current_streak,
                "longest": streak.longest_streak,
                "active_today": streak.last_active_date == today,
            },
        }
        return Response(GamificationSerializer(payload).data)
