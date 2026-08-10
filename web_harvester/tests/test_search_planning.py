from django.test import SimpleTestCase

from web_harvester.search_planning import SearchFact, SearchTarget, build_queries


class SearchPlanningTests(SimpleTestCase):
    def setUp(self):
        self.target = SearchTarget(
            profession_name="Mathematics teacher",
            program_group_code="B009",
            program_group_name="Mathematics teacher training",
            year=2026,
            legacy_codes=("5B010900", " 5b010900 "),
            alternative_names=("Учитель математики", "Математика мұғалімі"),
        )

    def test_planner_creates_every_fact_type_with_explicit_year(self):
        queries = build_queries(self.target)

        self.assertEqual({query.fact for query in queries}, set(SearchFact))
        self.assertTrue(all("2026" in query.text for query in queries))
        self.assertTrue(all(query.text.count('"') == 2 for query in queries))

    def test_legacy_codes_are_used_for_historical_not_current_legal_queries(self):
        queries = build_queries(self.target)
        legacy_queries = [query for query in queries if "5B010900" in query.text.upper()]

        self.assertTrue(legacy_queries)
        self.assertEqual(
            {query.fact for query in legacy_queries},
            {SearchFact.HISTORICAL_GRANT_CUTOFF},
        )

    def test_planner_deduplicates_aliases_and_bounds_each_fact(self):
        queries = build_queries(self.target)

        for fact in SearchFact:
            fact_queries = [query for query in queries if query.fact == fact]
            self.assertLessEqual(len(fact_queries), 4)
            self.assertEqual(
                len({query.text.casefold() for query in fact_queries}),
                len(fact_queries),
            )

    def test_specific_fact_selection_avoids_unrequested_queries(self):
        queries = build_queries(self.target, facts=(SearchFact.PROFILE_SUBJECTS,))

        self.assertTrue(queries)
        self.assertEqual(
            {query.fact for query in queries},
            {SearchFact.PROFILE_SUBJECTS},
        )

    def test_target_rejects_missing_identity_and_invalid_year(self):
        with self.assertRaises(ValueError):
            SearchTarget("", "B009", "Teacher training", 2026)
        with self.assertRaises(ValueError):
            SearchTarget("Teacher", "B009", "Teacher training", 1999)
