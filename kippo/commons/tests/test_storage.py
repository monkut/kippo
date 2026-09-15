import re
import tempfile
from http import HTTPStatus
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from whitenoise.middleware import WhiteNoiseMiddleware

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

    def test_post_process_precompresses_ui_paths_without_hashing(self) -> None:
        # Vite output must not be re-hashed, but WhiteNoise should still write a .gz sibling
        # so the middleware serves a pre-compressed body (kiconiaworks/kippo#61).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "ui/assets/entry.client-B8yIs_ty.js"
            (root / "ui" / "assets").mkdir(parents=True)
            (root / name).write_bytes(b"console.log('vite');\n" * 200)
            storage = KippoStaticFilesStorage(location=tmp)

            results = list(storage.post_process({name: (storage, name)}))

            written = sorted(p.name for p in (root / "ui" / "assets").iterdir())
            assert written == ["entry.client-B8yIs_ty.js", "entry.client-B8yIs_ty.js.gz"], (
                f"ui/ assets must get a .gz sibling and must not be re-hashed, got {written}"
            )
            assert (name, f"{name}.gz", True) in results

    def test_post_process_dry_run_writes_nothing_for_ui_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "ui/assets/entry.client-B8yIs_ty.js"
            (root / "ui" / "assets").mkdir(parents=True)
            (root / name).write_bytes(b"console.log('vite');\n" * 200)
            storage = KippoStaticFilesStorage(location=tmp)

            list(storage.post_process({name: (storage, name)}, dry_run=True))

            assert not (root / f"{name}.gz").exists()


class ImmutableFileTestSettingTestCase(SimpleTestCase):
    """WHITENOISE_IMMUTABLE_FILE_TEST must accept both Django's and Vite's hashed filenames."""

    def setUp(self) -> None:
        self.pattern = re.compile(settings.WHITENOISE_IMMUTABLE_FILE_TEST)

    def test_matches_vite_hashed_ui_assets(self) -> None:
        for url in (
            "/prod/static/ui/assets/entry.client-DzqYHiS7.js",
            "/prod/static/ui/assets/root-B0_D0xfK.css",
            "/prod/static/ui/assets/inter-latin-wght-normal-Dx4kXJAl.woff2",
            "/static/ui/assets/manifest-cd58e424.js",
        ):
            assert self.pattern.search(url), url

    def test_matches_django_manifest_hashed_assets(self) -> None:
        for url in (
            "/prod/static/admin/css/base.96c479cedf7a.css",
            "/static/rest_framework/css/bootstrap.min.cafbda9c0e9e.css.map",
        ):
            assert self.pattern.search(url), url

    def test_rejects_unhashed_files(self) -> None:
        for url in (
            "/prod/static/ui/index.html",
            "/prod/static/admin/css/base.css",
            "/prod/static/google_signin_buttons/web/1x/btn_google_signin_dark_normal_web.png",
        ):
            assert not self.pattern.search(url), url


class WhiteNoiseStagePrefixTestCase(SimpleTestCase):
    """Deployed configuration: URL_PREFIX=/prod → STATIC_URL=/prod/static/, while API Gateway strips
    the stage from path_info so Django (and WhiteNoise) see `/static/...`. WHITENOISE_STATIC_PREFIX
    is pinned to `/static/` for that reason (whitenoise#164); the immutable test must still fire.
    """

    UI_ASSET = "ui/assets/entry.client-B8yIs_ty.js"
    ADMIN_ASSET = "admin/css/base.96c479cedf7a.css"
    UNHASHED = "admin/css/base.css"

    def _cache_control(self, static_root: str, path: str) -> str | None:
        with override_settings(
            DEBUG=False,
            STATIC_ROOT=static_root,
            STATIC_URL="/prod/static/",
            WHITENOISE_STATIC_PREFIX="/static/",
            WHITENOISE_USE_FINDERS=False,
            WHITENOISE_AUTOREFRESH=False,
        ):
            middleware = WhiteNoiseMiddleware(lambda _request: HttpResponse("fallthrough"))
            # Stage already stripped by API Gateway: no /prod in path_info.
            response = middleware(RequestFactory().get(f"/static/{path}"))
        assert response.status_code == HTTPStatus.OK, f"{path}: expected WhiteNoise to serve it, got {response.status_code}"
        return response.get("Cache-Control")

    def test_stage_stripped_ui_asset_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (self.UI_ASSET, self.ADMIN_ASSET, self.UNHASHED):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(b"x")

            assert self._cache_control(tmp, self.UI_ASSET) == "max-age=315360000, public, immutable"
            assert self._cache_control(tmp, self.ADMIN_ASSET) == "max-age=315360000, public, immutable"
            assert self._cache_control(tmp, self.UNHASHED) == "max-age=60, public"
