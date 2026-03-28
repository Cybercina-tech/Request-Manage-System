"""
Canonical paths for static assets (fonts, vendor bundles).

Layout (under BASE_DIR):

- ``static/fonts/banner/`` — TrueType fonts for Pillow / ad banners (Yekan, monstrat, …)
- ``static/fonts/web/`` — UI fonts for CSS (@font-face): ``web/inter/``, ``web/persian/``
- ``static/vendor/fontawesome/`` — third-party CSS + ``webfonts/``

Legacy flat ``static/fonts/*.ttf`` is still tried when resolving files for zero-downtime deploys.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings


def base_dir() -> Path:
    return Path(settings.BASE_DIR)


def banner_font_dir() -> Path:
    return base_dir() / "static" / "fonts" / "banner"


def web_font_dir() -> Path:
    return base_dir() / "static" / "fonts" / "web"


def legacy_font_dir() -> Path:
    return base_dir() / "static" / "fonts"


def banner_font_candidates(filename: str) -> list[Path]:
    """Ordered search paths for a banner font file name (e.g. ``YekanBakh-Bold.ttf``)."""
    name = Path(filename).name
    return [
        banner_font_dir() / name,
        legacy_font_dir() / name,
        base_dir() / name,
    ]


def resolve_font_path(rel: str | None) -> Path | None:
    """
    Resolve a project-relative font path (e.g. from ``banner_config.json``).
    If the exact path is missing, tries ``static/fonts/banner/<basename>`` and legacy ``static/fonts/``.
    """
    if not rel or not str(rel).strip():
        return None
    rel_clean = str(rel).strip().replace("\\", "/")
    base = base_dir()
    primary = (base / rel_clean).resolve()
    if primary.exists():
        return primary
    name = Path(rel_clean).name
    for alt in (banner_font_dir() / name, legacy_font_dir() / name, base / name):
        if alt.exists():
            return alt
    return None
