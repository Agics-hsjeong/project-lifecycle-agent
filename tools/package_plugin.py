"""Create a shareable local plugin archive; exclude all customer data."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json
import os
import stat
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = (".codex-plugin", ".claude-plugin", ".github", "skills", "blueprint", "tools", "tests", "docs")
FILES = ("plugin.json", "README.md", "CHANGELOG.md", "AGENTS.md", ".gitignore", ".gitattributes", "LICENSE", "CONTRIBUTING.md", ".agents/plugins/marketplace.json")


def package(output=None):
    metadata = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    manifests = [metadata] + [json.loads((ROOT / name).read_text(encoding="utf-8")) for name in ("plugin.json", ".claude-plugin/plugin.json")]
    if len({(item["name"], item["version"]) for item in manifests}) != 1:
        raise ValueError("호스트별 플러그인 이름·버전이 다릅니다.")
    if json.loads((ROOT / "blueprint/project.json").read_text(encoding="utf-8"))["starter_version"] != metadata["version"]:
        raise ValueError("기본형과 플러그인 버전이 다릅니다.")
    for resource in ("tools/starter.py", "tools/lifecycle.py", "tools/storage.py", "tools/intake.py", "tools/proposal.py", "tools/reference_templates.py", "tools/template_analysis.py", "blueprint/proposal/package.json", "skills/project-lifecycle/SKILL.md"):
        if not (ROOT / resource).is_file():
            raise ValueError(f"필수 리소스 누락: {resource}")
    destination = Path(output) if output else ROOT / "dist" / f"{metadata['name']}-v{metadata['version']}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    prefix = Path(metadata["name"])
    paths = [ROOT / item for item in FILES]
    for item in FOLDERS:
        paths.extend(path for path in (ROOT / item).rglob("*") if "__pycache__" not in path.parts and path.suffix != ".pyc")
    for path in paths:
        current = path
        while current != ROOT:
            attributes = getattr(current.lstat(), "st_file_attributes", 0)
            if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("연결된 파일·폴더는 패키징할 수 없습니다.")
            current = current.parent
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError("플러그인 외부 파일은 패키징할 수 없습니다.")
    contents = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in sorted(paths) if path.is_file()}
    contents["PACKAGE.json"] = (json.dumps({"schema_version": 1, "name": metadata["name"], "version": metadata["version"],
        "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}}, sort_keys=True, indent=2) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".package-", suffix=".zip", dir=destination.parent)
    os.close(fd)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            from zipfile import ZipInfo
            for name, content in sorted(contents.items()):
                info = ZipInfo((prefix / name).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP 검증 실패")
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"검증 완료: {len(contents)} files — {destination}")
    return destination


if __name__ == "__main__":
    package()
