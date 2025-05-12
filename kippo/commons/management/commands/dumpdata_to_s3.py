"""Dump 'projects' content to s3"""

import gzip
from argparse import ArgumentParser
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from kippo.awsclients import S3_RESOURCE


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: ArgumentParser):
        parser.add_argument("-b", "--bucket", type=str, default=settings.DUMPDATA_S3_BUCKETNAME, required=False, help="S3 Bucket Name")

    def handle(self, *args, **options):
        s3_bucket_name = options["bucket"]
        if not s3_bucket_name:
            raise CommandError("`--bucket` not given or default not configured in settings.DUMPDATA_S3_BUCKETNAME!")

        self.stdout.write('Collecting "project" related data from Database...')

        apps = (
            "accounts",
            "octocat",
            "projects",
            "tasks",
            "social_django",
        )
        start = timezone.now()
        with TemporaryDirectory() as tmpdir:
            datetime_str = timezone.now().strftime("%Y%m%d_%H%M%S")
            filename = f"all_{datetime_str}.json.gz"
            output_filepath = Path(tmpdir).resolve() / filename
            self.stdout.write(f"Dumping to: {output_filepath}")
            with gzip.open(output_filepath, "wt", compresslevel=9) as output_file:
                call_command("dumpdata", *apps, indent=4, stdout=output_file, traceback=True)

            with output_filepath.open("rb") as upload_f:
                s3_key = f"{settings.DUMPDATA_S3_KEY_PREFIX}{filename}"
                s3_uri = f"s3://{s3_bucket_name}/{s3_key}"
                checkpoint = timezone.now()
                checkpoint_elapsed = checkpoint - start
                self.stdout.write(f"> Checkpoint Elapsed: {checkpoint_elapsed}")

                self.stdout.write(f'Writing "project" db dump to: {s3_uri}')
                S3_RESOURCE.Bucket(s3_bucket_name).put_object(Key=s3_key, Body=upload_f)
                end = timezone.now()
                total_elapsed = end - start
                self.stdout.write(f"> Total Elapsed: {total_elapsed}\n")

        self.stdout.write("Download with command: ")
        self.stdout.write(f"aws s3 cp s3://{s3_bucket_name}/{s3_key} .")
