"""Create a shareable local plugin archive; exclude all customer data."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import json

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = (".codex-plugin", ".claude-plugin", ".github", "skills", "blueprint", "tools", "tests", "docs")
FILES = ("plugin.json", "README.md", "CHANGELOG.md", "AGENTS.md", ".gitignore", ".gitattributes", "LICENSE", "CONTRIBUTING.md", ".agents/plugins/marketplace.json")


def package(output=None):
    metadata = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    destination = Path(output) if output else ROOT / "dist" / f"{metadata['name']}-v{metadata['version']}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    prefix = Path(metadata["name"])
    paths = [ROOT / item for item in FILES]
    for item in FOLDERS:
        paths.extend(path for path in (ROOT / item).rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    for path in paths:
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError("플러그인 외부 파일은 패키징할 수 없습니다.")
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, prefix / path.relative_to(ROOT))
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP 검증 실패")
        print(f"검증 완료: {len(archive.namelist())} files — {destination}")
    return destination


if __name__ == "__main__":
    package()
