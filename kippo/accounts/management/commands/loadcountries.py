"""
Load country data to db.
> originally taken from https://github.com/lukes/ISO-3166-Countries-with-Regional-Codes/blob/master/all/all.csv
"""

import csv
from argparse import ArgumentParser
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from accounts.models import Country

ACCOUNTS_FIXTURES_DIRECTORY = Path(__file__).parent.parent.parent.absolute() / "fixtures"
DEFAULT_COUNTRIES_CSV_FILEPATH = ACCOUNTS_FIXTURES_DIRECTORY / "countries.csv"


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: ArgumentParser):
        parser.add_argument(
            "-f",
            "--filepath",
            type=Path,
            default=DEFAULT_COUNTRIES_CSV_FILEPATH,
            required=False,
            help=_("(optional) provide countries csv filepath to load from"),
        )

    def handle(self, *args, **options):
        countries_csv_filepath = options["filepath"]
        if not countries_csv_filepath.exists():
            raise CommandError(f"file does not exists: {countries_csv_filepath}")

        countries = []
        with countries_csv_filepath.open("r", encoding="utf8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row:
                    c = Country(
                        name=row["name"],
                        alpha_2=row["alpha-2"],
                        alpha_3=row["alpha-3"],
                        country_code=row["country-code"],
                        region=row["region"],
                    )
                    countries.append(c)
        self.stdout.write(f"loading Countries ({len(countries)}) ...\n")
        Country.objects.bulk_create(countries)
        self.stdout.write(f"loading Countries ({len(countries)}) ... COMPLETE\n")
