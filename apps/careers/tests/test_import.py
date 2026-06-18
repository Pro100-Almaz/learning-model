"""Tests for the universities Excel import resource (T-302)."""

from __future__ import annotations

import tablib
from django.test import TestCase

from apps.careers.models import GrantThreshold, Specialty, University
from apps.careers.resources import UniversityImportResource


def _dataset(rows: list[dict]) -> tablib.Dataset:
    headers = [
        "university_code",
        "university_name",
        "city",
        "specialty_code",
        "specialty_name",
        "year",
        "min_score",
    ]
    ds = tablib.Dataset(headers=headers)
    for row in rows:
        ds.append([row[h] for h in headers])
    return ds


class UniversityImportResourceTests(TestCase):
    def test_imports_creates_university_specialty_and_threshold(self):
        ds = _dataset(
            [
                {
                    "university_code": "KBTU",
                    "university_name": "KBTU",
                    "city": "Алматы",
                    "specialty_code": "6B06",
                    "specialty_name": "Information Systems",
                    "year": 2024,
                    "min_score": 118,
                }
            ]
        )

        result = UniversityImportResource().import_data(ds, dry_run=False, raise_errors=True)

        self.assertFalse(result.has_errors())
        self.assertEqual(University.objects.count(), 1)
        self.assertEqual(Specialty.objects.count(), 1)
        self.assertEqual(GrantThreshold.objects.count(), 1)

        uni = University.objects.get(code="KBTU")
        self.assertEqual(uni.name, "KBTU")
        self.assertEqual(uni.city, "Алматы")
        sp = Specialty.objects.get(university=uni, code="6B06")
        self.assertEqual(sp.name, "Information Systems")
        thr = GrantThreshold.objects.get(specialty=sp, year=2024)
        self.assertEqual(thr.min_score, 118)

    def test_imports_upserts_existing_rows(self):
        # Pre-existing data with different values.
        uni = University.objects.create(name="Old Name", city="Old City", code="ENU")
        sp = Specialty.objects.create(university=uni, name="Old Spec", code="6B061")
        GrantThreshold.objects.create(specialty=sp, year=2024, min_score=10)

        ds = _dataset(
            [
                {
                    "university_code": "ENU",
                    "university_name": "ЕНУ им. Гумилёва",
                    "city": "Астана",
                    "specialty_code": "6B061",
                    "specialty_name": "Software Engineering",
                    "year": 2024,
                    "min_score": 115,
                }
            ]
        )
        result = UniversityImportResource().import_data(ds, dry_run=False, raise_errors=True)
        self.assertFalse(result.has_errors())

        uni.refresh_from_db()
        sp.refresh_from_db()
        self.assertEqual(uni.name, "ЕНУ им. Гумилёва")
        self.assertEqual(uni.city, "Астана")
        self.assertEqual(sp.name, "Software Engineering")

        self.assertEqual(GrantThreshold.objects.count(), 1)
        thr = GrantThreshold.objects.get(specialty=sp, year=2024)
        self.assertEqual(thr.min_score, 115)

    def test_multiple_years_per_specialty_are_preserved(self):
        ds = _dataset(
            [
                {
                    "university_code": "ENU",
                    "university_name": "ЕНУ",
                    "city": "Астана",
                    "specialty_code": "6B061",
                    "specialty_name": "SE",
                    "year": 2023,
                    "min_score": 100,
                },
                {
                    "university_code": "ENU",
                    "university_name": "ЕНУ",
                    "city": "Астана",
                    "specialty_code": "6B061",
                    "specialty_name": "SE",
                    "year": 2024,
                    "min_score": 115,
                },
            ]
        )

        result = UniversityImportResource().import_data(ds, dry_run=False, raise_errors=True)
        self.assertFalse(result.has_errors())
        self.assertEqual(University.objects.count(), 1)
        self.assertEqual(Specialty.objects.count(), 1)
        self.assertEqual(GrantThreshold.objects.count(), 2)
