import json
from argparse import ArgumentParser
from pathlib import Path

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management import BaseCommand
from django.utils.translation import gettext_lazy as _

from kippo.awsclients import S3_CLIENT

COMMANDS_DIR = Path(__file__).parent.resolve()
REQUIRED_BUCKET_NAMES = (settings.DUMPDATA_S3_BUCKETNAME,)


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser: ArgumentParser):
        parser.add_argument("--dry-run", action="store_true", default=False, help=_("If given buckets will NOT be created!"))

    def handle(self, *args, **options):
        for bucket_name in REQUIRED_BUCKET_NAMES:
            self.stdout.write(f"Creating Bucket({bucket_name})...")
            try:
                response = S3_CLIENT.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={
                        "LocationConstraint": settings.AWS_REGION,
                    },
                )
                self.stdout.write(str(response))
            except ClientError as e:
                if any(text in str(e.args) for text in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou")):
                    self.stderr.write(f"Creating Bucket({bucket_name})... ALREADY EXISTS!")
                else:
                    # not sure, re-raise
                    raise
            cors_config_filepath = COMMANDS_DIR / "s3-direct-bucket-cors.json"
            assert cors_config_filepath.exists(), f"{cors_config_filepath} not found!"
            cors_config_raw = cors_config_filepath.read_text(encoding="utf8")
            cors_config_json = json.loads(cors_config_raw)
            self.stdout.write(f"settings CORS for Bucket({bucket_name}) ...")
            S3_CLIENT.put_bucket_cors(Bucket=bucket_name, CORSConfiguration=cors_config_json)
            self.stdout.write(f"settings CORS for Bucket({bucket_name}) ... DONE!")
