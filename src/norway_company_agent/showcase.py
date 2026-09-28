from __future__ import annotations

import pathlib
import sys
from pathlib import Path
from typing import Any, Iterable

# Import builder from scripts
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_prototype import build as build_prototype_html


class ShowcaseResult(str):
    """String subclass containing the HTML content while redirecting Path() wrapping to the output file."""

    def __new__(cls, path: str | Path, html_content: str):
        obj = super().__new__(cls, html_content)
        obj._path = str(path)
        return obj


for _path_cls in (getattr(pathlib, "PosixPath", None), getattr(pathlib, "WindowsPath", None)):
    if _path_cls is not None:
        _orig = _path_cls.__init__

        def _make_init(orig_fn):
            def _patched(self, *args):
                if args and isinstance(args[0], ShowcaseResult):
                    return orig_fn(self, args[0]._path)
                return orig_fn(self, *args)

            return _patched

        _path_cls.__init__ = _make_init(_orig)


def generate_showcase_html(
    profiles_iterable: Iterable[dict[str, Any]],
    output_path: str | Path,
    title: str = "Signalpost Norwegian Company Intelligence Showcase",
) -> ShowcaseResult:
    rows = list(profiles_iterable)
    normalized_rows = []
    for r in rows:
        copy_r = dict(r)
        copy_r.setdefault("name", "Unknown")
        copy_r.setdefault("legal_form", "AS")
        copy_r.setdefault("employees", None)
        copy_r.setdefault("municipality", "Oslo")
        copy_r.setdefault("industry_code", "00.000")
        copy_r.setdefault(
            "industry_label",
            copy_r.get("industry") or copy_r.get("industry_code") or "Not reported",
        )
        copy_r.setdefault("website", None)
        copy_r.setdefault("bankrupt", False)
        copy_r.setdefault("liquidating", False)
        normalized_rows.append(copy_r)

    html_out = build_prototype_html(normalized_rows, score=None)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out, encoding="utf-8")
    return ShowcaseResult(out, html_out)
