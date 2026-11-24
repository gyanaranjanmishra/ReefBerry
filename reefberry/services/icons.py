"""Equipment icons.

Each icon is one SVG file, so any of them can be swapped later without touching code:

- Built-in icons live in reefberry/static/icons/<name>.svg.
- To replace one, or add a new one, put <name>.svg in the "icons" folder of the data
  directory (on the Pi: /var/lib/reefberry/icons/). A file there wins over the built-in
  one with the same name. New names appear in the icon picker in Settings.

Icons are inlined into the page so the dashboard can pause the animation and grey the
drawing when an outlet is off. Animations use the shared a-* classes defined in app.css
(a-spin, a-bub, a-flow, a-drip, ...); an icon can also carry its own <style>.
Only the Pi's owner can write to the data folder, so these files are trusted.
"""

from __future__ import annotations

import re
from pathlib import Path

from markupsafe import Markup

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
_SCRIPTY = re.compile(r"<script|\son\w+\s*=|javascript:", re.IGNORECASE)


class IconLibrary:
    def __init__(self, override_dir: Path | None = None):
        self.override_dir = override_dir
        self._cache: dict[Path, tuple[float, str]] = {}

    def _dirs(self) -> list[Path]:
        dirs = []
        if self.override_dir and self.override_dir.is_dir():
            dirs.append(self.override_dir)
        dirs.append(BUILTIN_DIR)
        return dirs

    def names(self) -> list[str]:
        found: set[str] = set()
        for d in self._dirs():
            found.update(p.stem for p in d.glob("*.svg") if _NAME.match(p.stem))
        return sorted(found)

    def path_for(self, name: str) -> Path | None:
        if not _NAME.match(name or ""):
            return None
        for d in self._dirs():
            p = d / f"{name}.svg"
            if p.is_file():
                return p
        return None

    def svg(self, name: str, fallback: str = "other") -> Markup:
        path = self.path_for(name) or self.path_for(fallback)
        if path is None:
            return Markup("")
        mtime = path.stat().st_mtime
        cached = self._cache.get(path)
        if cached and cached[0] == mtime:
            return Markup(cached[1])
        text = path.read_text(encoding="utf-8")
        start = text.find("<svg")
        text = text[start:] if start >= 0 else ""
        if _SCRIPTY.search(text):  # never inline anything that could run code
            text = ""
        self._cache[path] = (mtime, text)
        return Markup(text)
