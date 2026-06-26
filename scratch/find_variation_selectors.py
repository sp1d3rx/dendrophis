import os
from pathlib import Path


def find_selectors() -> None:
    project_dir = Path("/Users/derekw/Documents/projects/boiga")
    skip_dirs = {".git", ".venv", ".venv_pypy", ".ruff_cache", ".pytest_cache", ".mypy_cache"}
    for root, directories, files in os.walk(project_dir):
        # Modify directories in place to skip hidden/venv ones
        directories[:] = [d for d in directories if d not in skip_dirs and not d.startswith(".")]
        for filename in files:
            if filename.endswith((".py", ".md", ".sh", ".yaml", ".json", ".txt")):
                filepath = Path(root) / filename
                try:
                    content = filepath.read_text(encoding="utf-8")
                    if "\ufe0f" in content or "\ufe0e" in content:
                        print(f"Found in: {filepath}")
                        lines = content.splitlines()
                        for index, line in enumerate(lines):
                            if "\ufe0f" in line or "\ufe0e" in line:
                                print(f"  Line {index + 1}: {line!r}")
                except Exception:
                    # Ignore binary files or encoding issues
                    pass


if __name__ == "__main__":
    find_selectors()
