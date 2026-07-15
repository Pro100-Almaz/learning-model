from django.core.management.base import BaseCommand
from web_harvester.agents_web import search, extract
from web_harvester import trust, loader

PROFESSIONS = [
    ("Математика", "5В010900"),
    ("История", "5В011400"),
    ("Основы права и экономики", "5В011500"),
    ("Международное право", "5В030200"),
    ("Архитектура", "5В042000"),
]

class Command(BaseCommand):
    help = "Harvest ҰБТ score, subjects, and universities for each profession into the DB."
    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type = int, default = None,
            help = "Only process the first N professions (for quick test runs)."
        )
    def handle(self, *args, **options):
        limit = options["limit"]
        professions = PROFESSIONS[:limit]
        saved, skipped = 0, 0

        for name, national_code in professions:
            self.stdout.write(f"Harvesting: {name}({national_code}) ≽^•⩊•^≼")
            try:
                pages = search(name, national_code)

                url_to_content = {url: content for url, content in pages}
                ranked_urls = trust.filter_and_rank(list(url_to_content.keys()))
                filtered_pages = [(u, url_to_content[u]) for u in ranked_urls]

                if not filtered_pages:
                    self.stdout.write(self.style.WARNING("  no trusted sources — skipped"))
                    skipped += 1
                    continue

                result = extract(name, national_code, filtered_pages)
                if result is None:
                    self.stdout.write(self.style.WARNING("  extraction failed — skipped"))
                    skipped += 1
                    continue

                obj = loader.save(name, national_code, result)
                if obj is None:
                    self.stdout.write(self.style.WARNING("  not saved (untrusted) — skipped"))
                    skipped += 1
                    continue

                self.stdout.write(
                    self.style.SUCCESS(f"  saved (tier {obj.source_tier}, {obj.confidence})")
                )
                saved += 1

            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  error on {name}: {e}"))
                skipped += 1

        self.stdout.write(self.style.SUCCESS(f"Done. Saved {saved}, skipped {skipped}. ( -_•)ᡕᠵデᡁ᠊╾━💥"))
