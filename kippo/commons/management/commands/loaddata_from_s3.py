"""Dump 'projects' content to s3"""

from argparse import ArgumentParser
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from kippo.awsclients import S3_CLIENT


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: ArgumentParser):
        parser.add_argument("-b", "--bucket", type=str, default=settings.DUMPDATA_S3_BUCKETNAME, required=False, help="S3 Bucket Name")
        parser.add_argument("-k", "--s3-key", type=str, default=None, required=True, help=_("JSON Dump Gzip filepath"))

    def handle(self, *args, **options):
        s3_key = options["s3_key"]
        s3_bucket_name = options["bucket"]
        if not s3_bucket_name:
            raise CommandError("settings.DUMPDATA_S3_BUCKETNAME not configured!")

        with TemporaryDirectory() as tmpdir:
            filename = s3_key.replace(settings.DUMPDATA_S3_KEY_PREFIX, "")
            output_filepath = Path(tmpdir).resolve() / filename

            # Download the file from S3
            # -- lambda has a default 512 MB in /tmp
            self.stdout.write(f"Downloading from S3: s3://{s3_bucket_name}/{s3_key} -> {tmpdir}/{s3_key} ...")
            S3_CLIENT.download_file(s3_bucket_name, s3_key, str(output_filepath))
            self.stdout.write(f"Downloading from S3: s3://{s3_bucket_name}/{s3_key} -> {tmpdir}/{s3_key} ... DONE")

            self.stdout.write("Loadding data ...")
            call_command("loaddata", str(output_filepath), traceback=True)
            self.stdout.write("Loadding data ... DONE")
