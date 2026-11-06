"""Package source for deployment without local tokens, databases or caches."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parent.parent
target = root / "dist" / "camp-bot.zip"
target.parent.mkdir(exist_ok=True)
excluded = {".venv", ".git", ".pytest_cache", "__pycache__", "dist", "data", "logs"}
with ZipFile(target, "w", ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (path.is_file() and not set(relative.parts) & excluded and path.name != ".env"
                and ".sqlite3" not in path.name and path.suffix != ".pyc"):
            archive.write(path, Path("camp-bot") / relative)
with ZipFile(target) as archive:
    assert archive.testzip() is None
    assert not any(Path(n).name == ".env" or ".sqlite3" in n for n in archive.namelist())
    print(f"{target}: {len(archive.namelist())} files; no secrets or live database")
