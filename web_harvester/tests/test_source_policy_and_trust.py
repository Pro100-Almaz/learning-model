from unittest import TestCase

from web_harvester import trust
from web_harvester.source_policy import (
    COMMON_PRIMARY_DOMAINS,
    FALLBACK_DOMAINS,
    FIELD_PRIMARY_DOMAINS,
    FieldType,
    SourceStrategy,
)


class SourcePolicyTests(TestCase):
    def test_retired_primary_domains_are_not_configured(self):
        primary_domains = set(COMMON_PRIMARY_DOMAINS)
        for domains in FIELD_PRIMARY_DOMAINS.values():
            primary_domains.update(domains)

        retired_domains = {
            "academy.knb.kz",
            "almaty.mvd.kz",
            "amu.kz",
            "aues.edu.kz",
            "kaznau.kz",
            "kaznpu.kz",
            "kaznui.kz",
            "korkyt.kz",
            "mil.kz",
            "wkau.kz",
            "wksu.kz",
            "zkmu.edu.kz",
        }
        self.assertTrue(primary_domains.isdisjoint(retired_domains))

    def test_current_official_domains_are_assigned_to_the_expected_fields(self):
        expected_domains = {
            FieldType.MEDICINE: {"amu.edu.kz", "ospanov.university"},
            FieldType.EDUCATION: {
                "abai.university",
                "korkyt.edu.kz",
                "wku.edu.kz",
            },
            FieldType.TECHNICAL: {"energo.university"},
            FieldType.CREATIVE: {"kaznui.edu.kz"},
            FieldType.AGRICULTURE: {"kaznaru.edu.kz", "wkatu.edu.kz"},
            FieldType.MILITARY_AND_SECURITY: {"alpolac.edu.kz"},
        }
        for field_type, domains in expected_domains.items():
            with self.subTest(field_type=field_type):
                self.assertTrue(domains.issubset(FIELD_PRIMARY_DOMAINS[field_type]))

    def test_every_field_has_an_immutable_primary_domain_set(self):
        self.assertEqual(set(FIELD_PRIMARY_DOMAINS), set(FieldType))
        for domains in FIELD_PRIMARY_DOMAINS.values():
            self.assertIsInstance(domains, frozenset)
            self.assertTrue(domains)

    def test_source_groups_do_not_overlap(self):
        primary_domains = set(COMMON_PRIMARY_DOMAINS)
        for domains in FIELD_PRIMARY_DOMAINS.values():
            primary_domains.update(domains)

        self.assertTrue(primary_domains.isdisjoint(FALLBACK_DOMAINS))

    def test_all_configured_domains_are_normalized(self):
        domain_sets = [
            COMMON_PRIMARY_DOMAINS,
            FALLBACK_DOMAINS,
            *FIELD_PRIMARY_DOMAINS.values(),
        ]
        for domains in domain_sets:
            for domain in domains:
                with self.subTest(domain=domain):
                    self.assertEqual(domain, domain.lower())
                    self.assertFalse(domain.startswith("www."))
                    self.assertNotIn("://", domain)
                    self.assertNotIn("/", domain)

    def test_field_mapping_and_domain_sets_are_immutable(self):
        with self.assertRaises(TypeError):
            FIELD_PRIMARY_DOMAINS[FieldType.MEDICINE] = frozenset()
        with self.assertRaises(AttributeError):
            FIELD_PRIMARY_DOMAINS[FieldType.MEDICINE].add("example.kz")


class TrustTests(TestCase):
    def test_domain_normalization(self):
        cases = {
            " HTTPS://WWW.KAZNMU.EDU.KZ./page ": "kaznmu.edu.kz",
            "kaznmu.edu.kz/page": "kaznmu.edu.kz",
            "//Admissions.KAZNMU.EDU.KZ/path": "admissions.kaznmu.edu.kz",
            "": "",
            "https://[broken": "",
            "ftp://kaznmu.edu.kz/page": "",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(trust.domain_of(url), expected)

    def test_primary_accepts_exact_common_and_real_subdomains(self):
        field_type = FieldType.MEDICINE
        strategy = SourceStrategy.PRIMARY

        self.assertTrue(
            trust.is_allowed_source("https://kaznmu.edu.kz/a", field_type, strategy)
        )
        self.assertTrue(
            trust.is_allowed_source(
                "https://admissions.kaznmu.edu.kz/a",
                field_type,
                strategy,
            )
        )
        self.assertTrue(
            trust.is_allowed_source("https://testcenter.kz/a", field_type, strategy)
        )

    def test_primary_rejects_lookalike_cross_field_and_fallback_domains(self):
        field_type = FieldType.MEDICINE
        strategy = SourceStrategy.PRIMARY

        rejected = [
            "https://kaznmu.edu.kz.evil.com/a",
            "https://fakekaznmu.edu.kz/a",
            "https://kbtu.edu.kz/a",
            "https://univision.kz/a",
        ]
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(trust.is_allowed_source(url, field_type, strategy))

    def test_fallback_is_separate_from_primary(self):
        field_type = FieldType.MEDICINE
        fallback_url = "https://univision.kz/a"

        self.assertEqual(
            trust.strategy_of(fallback_url, field_type),
            SourceStrategy.FALLBACK,
        )
        self.assertFalse(
            trust.is_allowed_source(
                fallback_url,
                field_type,
                SourceStrategy.PRIMARY,
            )
        )

    def test_filter_sources_preserves_order_and_removes_duplicates(self):
        urls = [
            "https://kaznmu.edu.kz/a",
            "https://evil.kz/a",
            "https://testcenter.kz/b",
            "https://kaznmu.edu.kz/a",
        ]

        self.assertEqual(
            trust.filter_sources(
                urls,
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
            ),
            ["https://kaznmu.edu.kz/a", "https://testcenter.kz/b"],
        )

    def test_validate_sources_requires_nonempty_single_group(self):
        self.assertFalse(
            trust.validate_sources(
                [],
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
            )
        )
        self.assertTrue(
            trust.validate_sources(
                ["https://kaznmu.edu.kz/a", "https://testcenter.kz/b"],
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
            )
        )
        self.assertFalse(
            trust.validate_sources(
                ["https://kaznmu.edu.kz/a", "https://univision.kz/b"],
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
            )
        )

    def test_stamp_derives_strategy_metadata_and_rejects_invalid_sources(self):
        self.assertEqual(
            trust.stamp(
                ["https://kaznmu.edu.kz/a"],
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
            ),
            (1, "High"),
        )
        self.assertEqual(
            trust.stamp(
                ["https://univision.kz/a"],
                FieldType.MEDICINE,
                SourceStrategy.FALLBACK,
            ),
            (2, "Low"),
        )
        self.assertEqual(
            trust.stamp(
                ["https://kbtu.edu.kz/a"],
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
            ),
            (None, None),
        )
