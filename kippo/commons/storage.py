"""Static-files storage subclasses for kippo.

`KippoStaticFilesStorage` wires WhiteNoise's compressed-manifest backend into
Django's 5.1+ ``STORAGES`` API while leaving the kippo-ui SPA bundle untouched.
"""

from collections.abc import Iterator
from typing import Any

from django.core.files.base import File
from whitenoise.storage import CompressedManifestStaticFilesStorage


class KippoStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Manifest + compression for Django assets; bypasses both for the kippo-ui bundle.

    Vite pre-hashes the SPA assets (e.g. ``entry.client-B8yIs_ty.js``) and the
    SPA's ``index.html`` hard-codes those filenames as plain ``<script>`` /
    ``<link>`` tags. Letting Django re-hash them at ``collectstatic`` would 404
    every reference on ``/prod/ui/*``.

    The ``ui/`` prefix is therefore:
      * not hashed (``hashed_name`` returns the input verbatim), and
      * skipped from the manifest/URL-rewriting half of ``post_process``, while
        still being handed to WhiteNoise's compressor so ``.gz`` siblings exist
        and the middleware serves pre-compressed bodies (kiconiaworks/kippo#61).

    Admin and other Django-owned assets are hashed and compressed normally.
    ``manifest_strict = False`` lets any future ``{% static "ui/..." %}`` call
    fall back to the verbatim path instead of raising.
    """

    manifest_strict = False
    _UI_PREFIX = "ui/"

    def hashed_name(self, name: str, content: File | None = None, filename: str | None = None) -> str:
        if name.startswith(self._UI_PREFIX):
            return name
        return super().hashed_name(name, content, filename)

    def post_process(self, paths: dict[str, Any], **options: Any) -> Iterator[tuple[str, str, bool]]:  # noqa: ANN401
        django_paths = {k: v for k, v in paths.items() if not k.startswith(self._UI_PREFIX)}
        yield from super().post_process(django_paths, **options)
        if options.get("dry_run"):
            return
        ui_paths = [name for name in paths if name.startswith(self._UI_PREFIX)]
        for name, compressed_name in self.compress_files(ui_paths):
            yield name, compressed_name, True
