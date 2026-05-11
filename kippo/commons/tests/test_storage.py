from django.core.files.base import ContentFile
from django.test import SimpleTestCase

from commons.storage import KippoStaticFilesStorage


class KippoStaticFilesStorageTestCase(SimpleTestCase):
    def setUp(self) -> None:
        self.storage = KippoStaticFilesStorage()

    def test_hashed_name_passes_ui_paths_through_verbatim(self) -> None:
        # Vite-pre-hashed asset under the SPA bundle prefix.
        name = "ui/assets/entry.client-B8yIs_ty.js"

        result = self.storage.hashed_name(name, content=ContentFile(b"console.log('vite');"))

        assert result == name, (
            "ui/ paths must be returned verbatim so Vite's pre-hashed filenames are not "
            "double-hashed (which would 404 every <script>/<link> in the SPA's index.html)."
        )

    def test_hashed_name_hashes_admin_paths(self) -> None:
        name = "admin/css/base.css"
        content = ContentFile(b"body { color: red; }")

        result = self.storage.hashed_name(name, content=content)

        assert result != name, "Admin assets must be content-hashed for cache-busting."
        assert result.startswith("admin/css/base."), f"Expected hashed name to keep prefix and ext, got {result!r}"
        assert result.endswith(".css"), f"Expected hashed name to keep .css ext, got {result!r}"

    def test_manifest_strict_is_false(self) -> None:
        # manifest_strict=False lets {% static "ui/..." %} lookups fall back gracefully
        # when the path is intentionally absent from staticfiles.json.
        assert self.storage.manifest_strict is False
