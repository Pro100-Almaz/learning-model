"""Serializers for /gamification/me/.

Shape is fixed by openapi.yaml — Gamification schema. snake_case JSON.
"""
from rest_framework import serializers


class StreakSerializer(serializers.Serializer):
    current = serializers.IntegerField()
    longest = serializers.IntegerField()
    active_today = serializers.BooleanField()


class GamificationSerializer(serializers.Serializer):
    total_xp = serializers.IntegerField()
    level_code = serializers.CharField()
    level_label = serializers.CharField()
    xp_to_next_level = serializers.IntegerField()
    streak = StreakSerializer()
