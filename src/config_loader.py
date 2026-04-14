from __future__ import annotations

from pathlib import Path


def load_config(config_path: str | Path) -> dict:
    import yaml

    path = Path(config_path)
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def resolve_project_path(project_root: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    return path if path.is_absolute() else (project_root / path).resolve()


def ensure_directories(config: dict, project_root: Path) -> None:
    for section, key in [("review", "output_dir"), ("logs", "output_dir"), ("rollback", "output_dir"), ("temp", "dir")]:
        resolve_project_path(project_root, config[section][key]).mkdir(parents=True, exist_ok=True)
