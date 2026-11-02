"""Check release metadata without third-party packages or running external tools."""
import argparse
import hashlib
import json
from pathlib import Path


def check(root=None, site=False):
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))
    manifests = [read(n) for n in ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json")]
    version = manifests[0]["version"]
    checks = {"plugin_identity": len({m["name"] for m in manifests}) == 1,
              "plugin_versions": all(m["version"] == version for m in manifests),
              "blueprint_version": read("blueprint/project.json")["starter_version"] == version,
              "readme_version": version in (root / "README.md").read_text(encoding="utf-8"),
              "implementation_version": version in (root / "docs/V2-IMPLEMENTATION.md").read_text(encoding="utf-8")}
    if site:
        metadata = read("website/public/downloads/manual-metadata.json")
        pdf = root / "website/public/downloads/project-ieum-user-guide.pdf"
        checks.update(website_version=read("website/package.json")["version"] == version,
                      website_lock_version=read("website/package-lock.json")["version"] == version,
                      manual_version=metadata.get("version") == version,
                      manual_hash=hashlib.sha256(pdf.read_bytes()).hexdigest() == metadata.get("sha256"),
                      manual_bytes=pdf.stat().st_size == metadata.get("bytes"))
    return {"ready": all(checks.values()), "version": version, "checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", action="store_true", help="Check website and manual metadata too")
    args = parser.parse_args()
    try:
        result = check(site=args.site)
    except (OSError, ValueError, KeyError) as exc:
        result = {"ready": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ready"] else 1)
