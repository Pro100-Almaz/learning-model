"""django-import-export resource for the universities Excel sheet.

One row per (university, specialty, year). Columns (per plan/05_Seed_Data_Spec.md):

    university_code | university_name | city |
    specialty_code  | specialty_name  | year | min_score

The importer upserts University → Specialty → GrantThreshold keyed by code +
year. It is intentionally row-driven (not a ModelResource) so a single .xlsx
populates three models in one pass.
"""

from __future__ import annotations

from import_export import fields, resources
from import_export.results import RowResult

from apps.careers.models import GrantThreshold, Specialty, University


REQUIRED_COLUMNS = (
    "university_code",
    "university_name",
    "city",
    "specialty_code",
    "specialty_name",
    "year",
    "min_score",
)


class UniversityImportResource(resources.ModelResource):
    """Row-oriented importer that upserts University/Specialty/GrantThreshold."""

    university_code = fields.Field(column_name="university_code")
    university_name = fields.Field(column_name="university_name")
    city = fields.Field(column_name="city")
    specialty_code = fields.Field(column_name="specialty_code")
    specialty_name = fields.Field(column_name="specialty_name")
    year = fields.Field(column_name="year")
    min_score = fields.Field(column_name="min_score")

    class Meta:
        # We manage persistence manually inside ``import_row`` — no single
        # underlying model.
        model = GrantThreshold
        use_transactions = True
        skip_unchanged = False
        report_skipped = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_str(value) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _clean_int(value) -> int:
        if value is None or value == "":
            raise ValueError("required integer is empty")
        return int(float(value))  # tolerate "118.0" from Excel

    @classmethod
    def _validate_row(cls, row: dict) -> None:
        missing = [c for c in REQUIRED_COLUMNS if c not in row]
        if missing:
            raise ValueError(f"missing columns: {', '.join(missing)}")
        for col in (
            "university_code",
            "university_name",
            "city",
            "specialty_code",
            "specialty_name",
        ):
            if not cls._clean_str(row.get(col)):
                raise ValueError(f"empty value for required column '{col}'")

    # ------------------------------------------------------------------
    # Core upsert
    # ------------------------------------------------------------------

    def _upsert(self, row: dict) -> tuple[University, Specialty, GrantThreshold, bool]:
        self._validate_row(row)

        university_code = self._clean_str(row["university_code"])
        university_name = self._clean_str(row["university_name"])
        city = self._clean_str(row["city"])
        specialty_code = self._clean_str(row["specialty_code"])
        specialty_name = self._clean_str(row["specialty_name"])
        year = self._clean_int(row["year"])
        min_score = self._clean_int(row["min_score"])

        university, _ = University.objects.update_or_create(
            code=university_code,
            defaults={"name": university_name, "city": city},
        )
        specialty, _ = Specialty.objects.update_or_create(
            university=university,
            code=specialty_code,
            defaults={"name": specialty_name},
        )
        threshold, created = GrantThreshold.objects.update_or_create(
            specialty=specialty,
            year=year,
            defaults={"min_score": min_score},
        )
        return university, specialty, threshold, created

    # ------------------------------------------------------------------
    # django-import-export hooks
    # ------------------------------------------------------------------

    def import_row(self, row, instance_loader, **kwargs):  # noqa: ARG002
        result = RowResult()
        try:
            _, _, _, created = self._upsert(dict(row))
        except Exception as exc:  # noqa: BLE001
            result.import_type = RowResult.IMPORT_TYPE_ERROR
            result.errors.append(self.get_error_result_class()(exc, traceback="", row=row))
            return result

        result.import_type = (
            RowResult.IMPORT_TYPE_NEW if created else RowResult.IMPORT_TYPE_UPDATE
        )
        return result

    def get_instance(self, instance_loader, row):  # noqa: ARG002
        return None
