from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.careers.admission_services import latest_grant_cutoff_payload
from apps.careers.models import GrantThreshold, Specialty, University


class LatestGrantCutoffSerializer(serializers.Serializer):
    """Typed canonical grant cutoff attached to one specialty."""

    score = serializers.IntegerField()
    year = serializers.IntegerField()
    score_type = serializers.CharField()
    source_url = serializers.URLField()


class SpecialtySerializer(serializers.ModelSerializer):
    """Specialty + legacy latest_threshold + typed canonical latest_grant_cutoff."""

    university_id = serializers.IntegerField(read_only=True)
    latest_threshold = serializers.SerializerMethodField()
    latest_grant_cutoff = serializers.SerializerMethodField()

    class Meta:
        model = Specialty
        fields = (
            "id",
            "university_id",
            "name",
            "code",
            "latest_threshold",
            "latest_grant_cutoff",
        )

    def get_latest_threshold(self, obj: Specialty) -> int | None:
        """Legacy untyped number, kept only until the frontend migrates."""
        # Prefer prefetched thresholds (set by the view) to avoid N+1.
        thresholds = getattr(obj, "_prefetched_thresholds", None)
        if thresholds is None:
            thresholds = list(obj.thresholds.all())

        if not thresholds:
            return None
        latest = max(thresholds, key=lambda t: t.year)
        return int(latest.min_score)

    @extend_schema_field(LatestGrantCutoffSerializer(allow_null=True))
    def get_latest_grant_cutoff(self, obj: Specialty) -> dict | None:
        # Prefer prefetched canonical thresholds (set by the view) to avoid N+1.
        thresholds = getattr(obj, "_prefetched_admission_thresholds", None)
        if thresholds is None:
            thresholds = list(obj.admission_thresholds.all())
        return latest_grant_cutoff_payload(thresholds)


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
            sp._prefetched_admission_thresholds = list(sp.admission_thresholds.all())
        return SpecialtySerializer(specialties, many=True).data


class QualifyingGrantSerializer(serializers.Serializer):
    """Shape for one qualifying grant entry inside GrantCalcResult.

    The context fields are optional so an older client keeps working, but a
    canonical entry always carries them: a score without its type and year is
    not interpretable.
    """

    university_name = serializers.CharField(allow_blank=True)
    specialty_name = serializers.CharField()
    min_score = serializers.IntegerField()
    margin = serializers.IntegerField()
    year = serializers.IntegerField(required=False)
    score_type = serializers.CharField(required=False)
    program_group_code = serializers.CharField(required=False)
    source_url = serializers.URLField(required=False)


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
    "LatestGrantCutoffSerializer",
    "QualifyingGrantSerializer",
    "GoalTrackerSerializer",
    "GrantCalcResultSerializer",
]
