from rest_framework import serializers

from .models import GrantThreshold, Specialty, University


class SpecialtySerializer(serializers.ModelSerializer):
    """Specialty + latest threshold (max year), per openapi.yaml Specialty schema."""

    university_id = serializers.IntegerField(read_only=True)
    latest_threshold = serializers.SerializerMethodField()

    class Meta:
        model = Specialty
        fields = ("id", "university_id", "name", "code", "latest_threshold")

    def get_latest_threshold(self, obj: Specialty) -> int | None:
        # Prefer prefetched thresholds (set by the view) to avoid N+1.
        thresholds = getattr(obj, "_prefetched_thresholds", None)
        if thresholds is None:
            thresholds = list(obj.thresholds.all())

        if not thresholds:
            return None
        latest = max(thresholds, key=lambda t: t.year)
        return int(latest.min_score)


class UniversitySerializer(serializers.ModelSerializer):
    """University catalog item with specialties[] (each with latest_threshold)."""

    specialties = serializers.SerializerMethodField()

    class Meta:
        model = University
        fields = ("id", "name", "city", "code", "specialties")

    def get_specialties(self, obj: University) -> list[dict]:
        specialties = list(obj.specialties.all())
        # Attach prefetched thresholds to each specialty for the serializer.
        for sp in specialties:
            sp._prefetched_thresholds = list(sp.thresholds.all())
        return SpecialtySerializer(specialties, many=True).data


class QualifyingGrantSerializer(serializers.Serializer):
    """Shape for one qualifying grant entry inside GrantCalcResult."""

    university_name = serializers.CharField()
    specialty_name = serializers.CharField()
    min_score = serializers.IntegerField()
    margin = serializers.IntegerField()


class GoalTrackerSerializer(serializers.Serializer):
    """Shape for GoalTracker — appears under GrantCalcResult.goal when target set."""

    target_score = serializers.IntegerField()
    predicted_score = serializers.FloatField()
    gap = serializers.FloatField()
    weakest_tag = serializers.CharField(allow_null=True)
    advice = serializers.CharField()


class GrantCalcResultSerializer(serializers.Serializer):
    """Top-level GrantCalcResult — see openapi.yaml."""

    predicted_score = serializers.FloatField()
    math_score = serializers.FloatField()
    other_subjects_total = serializers.FloatField()
    qualifying_grants = QualifyingGrantSerializer(many=True)
    goal = GoalTrackerSerializer(allow_null=True)


__all__ = [
    "GrantThreshold",
    "UniversitySerializer",
    "SpecialtySerializer",
    "QualifyingGrantSerializer",
    "GoalTrackerSerializer",
    "GrantCalcResultSerializer",
]
