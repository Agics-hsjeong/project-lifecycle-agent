"""Bounded source snapshots, independent of installed Git or model APIs."""
from tools.intake import hash_file
from tools.storage import confined

LOCK_FILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt", "pyproject.toml", "uv.lock", "Cargo.lock", "go.sum")


def source_hashes(root, data):
    files = set()
    for relative in data.get("technical", {}).get("source_roots", ["src"]):
        parent = confined(root, relative)
        if parent.is_file():
            files.add(parent)
        elif parent.is_dir():
            files.update(path for path in parent.rglob("*") if path.is_file() and
                         not set(path.relative_to(parent).parts).intersection({"__pycache__", "node_modules", ".git", ".venv"}))
    files.update(root / name for name in LOCK_FILES if (root / name).is_file())
    if len(files) > 10000 or sum(path.stat().st_size for path in files) > 64 * 1024 * 1024:
        raise ValueError("코드 근거 스냅샷 제한 초과. source_roots 범위를 조정하세요.")
    result = {}
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        result[relative] = hash_file(confined(root, relative))
    return result
