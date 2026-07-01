"""Stage GitHub profile URLs/handles as ``.github`` source files for adapter routing."""

from __future__ import annotations

from pathlib import Path

from app.sources.github import handle_to_stem


def stage_github_handles(handles: list[str], tmp_dir: Path, start_index: int = 0) -> list[Path]:
    """Write each handle to its own nested ``.github`` file under ``tmp_dir``.

    Each handle gets a dedicated subdirectory so staged filenames never collide
    while preserving the ``.github`` suffix the :class:`GitHubAdapter` routes on.
    """
    paths: list[Path] = []
    for offset, handle in enumerate(handles):
        index = start_index + offset
        sub_dir = tmp_dir / f"gh_{index}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        path = sub_dir / f"{handle_to_stem(handle, f'github_{index}')}.github"
        path.write_text(handle, encoding="utf-8")
        paths.append(path)
    return paths
