"""Bundle the built workbench and its asset dependency closure; stdlib only.

Run after `npm run build` in website. Customer projects and website downloads
are deliberately outside the accepted input paths. `--check` verifies a bundle.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = "/project-lifecycle-agent/"
ENTRY = "workspace/index.html"
EXTENSIONS = {".html", ".js", ".mjs", ".css", ".woff", ".woff2", ".ttf", ".png", ".svg", ".ico", ".webp", ".wasm"}
# Astro's imports, HTML attributes, dynamic chunk names and CSS url() values.
REFS = re.compile(r"[\"'`]([^\"'`\s<>]+)[\"'`]|url\(\s*([^)'\"\s]+)\s*\)")


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def third_party_notices():
    """Retain notices of bundled runtime libraries and their npm dependencies."""
    lock_path = ROOT / "website/package-lock.json"
    modules = ROOT / "website/node_modules"
    if not lock_path.is_file() or not modules.is_dir():
        return None
    packages = json.loads(lock_path.read_text(encoding="utf-8")).get("packages", {})
    pending = ["react", "react-dom", "@xyflow/react", "elkjs", "cytoscape", "react-force-graph-3d"]
    seen, sections = set(), []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        meta = packages.get("node_modules/" + name)
        if not meta:
            continue
        pending.extend(meta.get("dependencies", {}))
        package_root = modules / name
        if not package_root.resolve().is_relative_to(modules.resolve()):
            raise ValueError("Unsafe npm license path")
        header = name + " " + meta.get("version", "") + " (" + str(meta.get("license", "see upstream")) + ")"
        texts = []
        for filename in ("LICENSE", "LICENSE.txt", "LICENSE.md", "license", "license.txt", "COPYING", "NOTICE", "NOTICE.md"):
            path = package_root / filename
            if path.is_file() and path.stat().st_size <= 256 * 1024:
                value = path.read_text(encoding="utf-8", errors="replace")
                if value not in texts:
                    texts.append(value)
        sections.append(header + "\n" + ("\n".join(texts) if texts else "License identifier above; upstream source: https://www.npmjs.com/package/" + name))
    return ("Project Ieum workbench — bundled third-party runtime notices\n\n" + "\n\n".join(sorted(sections)) + "\n").encode("utf-8")


def source_hashes():
    paths = [ROOT / p for p in ("website/package.json", "website/package-lock.json", "website/astro.config.mjs",
        "website/src/styles/tokens.css", "website/src/styles/fonts.css")]
    for directory in (ROOT / "website/src/workspace", ROOT / "website/src/pages/workspace"):
        if directory.is_dir():
            paths.extend(p for p in directory.rglob("*") if p.is_file())
    # Git normalizes text to LF; source fingerprints must survive Windows clones.
    return {p.relative_to(ROOT).as_posix(): checksum(p.read_text(encoding="utf-8").encode("utf-8"))
            for p in sorted(paths) if p.is_file()}


def verify(folder=None):
    folder = Path(folder) if folder else ROOT / "tools/workspace_ui"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("entry") != ENTRY or manifest.get("base") != BASE:
        raise ValueError("Invalid workspace asset manifest")
    hashes = manifest.get("sha256")
    if not isinstance(hashes, dict) or ENTRY not in hashes:
        raise ValueError("Workspace assets are missing")
    for name, expected in hashes.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe asset path")
        path = folder / relative
        if not path.resolve().is_relative_to(folder.resolve()) or path.is_symlink():
            raise ValueError("Linked asset is not allowed")
        if checksum(path.read_bytes()) != expected:
            raise ValueError("Workspace asset hash mismatch: " + name)
    if (ROOT / "website/package.json").is_file() and manifest.get("source_sha256") != source_hashes():
        raise ValueError("Workspace sources changed; build website and refresh the UI bundle")
    return {"ready": True, "files": len(hashes), "bytes": sum((folder / name).stat().st_size for name in hashes)}


def bundle(source=None, output=None):
    source = Path(source) if source else ROOT / "website/dist"
    output = Path(output) if output else ROOT / "tools/workspace_ui"
    source, output = source.resolve(), output.resolve()
    if not output.is_relative_to(ROOT / "tools"):
        raise ValueError("Bundle output must remain inside tools")
    entry_path = source / ENTRY
    if entry_path.is_file() and any((ROOT / name).stat().st_mtime_ns > entry_path.stat().st_mtime_ns
                                   for name in source_hashes()):
        raise ValueError("Workspace sources are newer than the build; run the website build first")
    pending, files = [ENTRY], {}
    if (source / "fonts/OFL.txt").is_file():
        pending.append("fonts/OFL.txt")
    while pending:
        name = pending.pop()
        if name in files:
            continue
        target = source / name
        if not target.resolve().is_relative_to(source) or target.is_symlink() or not target.is_file():
            raise ValueError("Missing or unsafe workspace dependency: " + name)
        content = target.read_bytes()
        files[name] = content
        if target.suffix not in {".html", ".js", ".mjs", ".css"}:
            continue
        for match in REFS.finditer(content.decode("utf-8")):
            value = match.group(1) or match.group(2)
            try:
                parsed = urlsplit(value)
            except ValueError:
                continue
            if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("#"):
                continue
            path = unquote(parsed.path)
            if path.startswith(BASE):
                relative = Path(path[len(BASE):])
            elif path.startswith(("/_astro/", "/fonts/", "/brand/")):
                relative = Path(path.lstrip("/"))
            elif path.startswith("/"):
                continue
            else:
                relative = Path(name).parent / path
            candidate = (source / relative).resolve()
            if candidate.suffix not in EXTENSIONS or not candidate.is_file():
                continue
            if not candidate.is_relative_to(source):
                raise ValueError("External workspace asset")
            key = candidate.relative_to(source).as_posix()
            if not key.startswith(("_astro/", "brand/", "fonts/", "workspace/")):
                raise ValueError("Unexpected workspace dependency: " + key)
            pending.append(key)
    notices = third_party_notices()
    if notices:
        files["THIRD-PARTY-LICENSES.txt"] = notices
    output.mkdir(parents=True, exist_ok=True)
    # Remove only files owned by the previous manifest, after validating its paths.
    previous_path = output / "manifest.json"
    if previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        for name in previous.get("sha256", {}):
            old = (output / name).resolve()
            if not old.is_relative_to(output) or old == output:
                raise ValueError("Unsafe previous asset path")
            if name not in files:
                old.unlink(missing_ok=True)
    for name, data in files.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {"schema_version": 1, "base": BASE, "entry": ENTRY,
                "sha256": {name: checksum(data) for name, data in sorted(files.items())}, "source_sha256": source_hashes()}
    previous_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verify(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.check else bundle(), ensure_ascii=False))
