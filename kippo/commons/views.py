"""Views for commons app."""

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.views import View


class SPAView(View):
    """Serve the SPA index.html for all UI routes.

    This view serves the React SPA's index.html file, allowing React Router
    to handle client-side routing. Static assets (JS, CSS) are still served
    from /static/ui/assets/ by whitenoise.
    """

    def get(self, request: HttpRequest, path: str = "") -> HttpResponse:
        """Serve the SPA index.html file."""
        # Look for index.html in STATIC_ROOT first (production), then source dir (development)
        static_root = Path(settings.STATIC_ROOT) if settings.STATIC_ROOT else None
        source_dir = getattr(settings, "UI_STATIC_SOURCE_DIR", None)

        index_paths = []
        if static_root:
            index_paths.append(static_root / "ui" / "index.html")
        if source_dir:
            index_paths.append(Path(source_dir) / "index.html")

        for index_path in index_paths:
            if index_path.exists():
                return FileResponse(
                    open(index_path, "rb"),  # noqa: SIM115, PTH123
                    content_type="text/html",
                )

        raise Http404("UI not installed. Run 'uv run poe update-ui' to install.")
