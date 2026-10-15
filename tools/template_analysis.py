"""Bounded OOXML observations, not a substitute for rendered visual inspection."""
from collections import Counter
from pathlib import Path, PurePosixPath
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from tools.intake import MAX_SOURCE_BYTES, MAX_XML_BYTES


def tag(node):
    return node.tag.rsplit("}", 1)[-1]


def attrs(node):
    return {key.rsplit("}", 1)[-1]: value for key, value in node.attrib.items()}


def xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError("템플릿 XML 크기 제한 초과")
    value = archive.read(name)
    normalized = value.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
        raise ValueError("DTD/엔터티가 있는 템플릿 XML은 자동 분석하지 않습니다.")
    return ET.fromstring(value)


def text(node, limit=600):
    return " ".join(item.text or "" for item in node.iter() if tag(item) == "t")[:limit]


def observations(roots):
    fonts, colors, sizes = Counter(), Counter(), Counter()
    for root in roots:
        for node in root.iter():
            local, values = tag(node), attrs(node)
            if local in {"rFonts", "latin", "ea", "cs"}:
                for key in ("ascii", "hAnsi", "eastAsia", "typeface"):
                    if values.get(key):
                        fonts[values[key]] += 1
            if local in {"color", "srgbClr"} and re.fullmatch(r"[0-9a-fA-F]{6}", values.get("val", "")):
                colors[values["val"].upper()] += 1
            if local in {"rPr", "defRPr", "endParaRPr"} and "sz" in values:
                sizes["pptx_hundredth_pt:" + values["sz"]] += 1
            if local == "sz" and "val" in values:
                sizes["docx_half_pt:" + values["val"]] += 1
    return {"fonts": dict(fonts.most_common(40)), "colors": dict(colors.most_common(40)), "font_sizes": dict(sizes.most_common(40))}


def slide_paths(archive, root):
    relations = xml(archive, "ppt/_rels/presentation.xml.rels")
    links = {item.attrib.get("Id"): item.attrib.get("Target") for item in relations if item.attrib.get("TargetMode") != "External"}
    result = []
    for item in root.iter():
        if tag(item) != "sldId":
            continue
        relation_id = next((value for key, value in item.attrib.items() if key.endswith("}id")), None)
        target = links.get(relation_id)
        if not target:
            raise ValueError("발표자료 슬라이드 연결 누락")
        name = posixpath.normpath("ppt/" + target) if not target.startswith("/") else target.lstrip("/")
        if ".." in PurePosixPath(name).parts or not name.startswith("ppt/slides/"):
            raise ValueError("잘못된 슬라이드 경로")
        result.append(name)
    return result


def analyze(path):
    path = Path(path)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("템플릿은 64 MiB 이하여야 합니다.")
    extension = path.suffix.lower()
    result = {"schema_version": 1, "format": extension, "status": "needs_reader", "editable": extension in {".docx", ".pptx"},
              "notice": "기계 관측값입니다. 상속 스타일·페이지 배치·도식·문체는 원본 렌더링과 대조해야 합니다."}
    if extension not in {".docx", ".dotx", ".pptx", ".potx"}:
        return result
    with ZipFile(path) as archive:
        relevant = [name for name in archive.namelist() if name.endswith(".xml") and
                    (name.startswith("word/") or name.startswith("ppt/") or name.startswith("docProps/"))]
        xml_parts = [name for name in archive.namelist() if name.endswith((".xml", ".rels"))]
        if sum(archive.getinfo(name).file_size for name in xml_parts) > MAX_XML_BYTES:
            raise ValueError("템플릿 XML 전체 크기 제한 초과")
        external = []
        for name in archive.namelist():
            if name.endswith(".rels"):
                for relation in xml(archive, name):
                    if relation.attrib.get("TargetMode") == "External":
                        external.append({"part": name, "target": relation.attrib.get("Target", "")[:500]})
        result["external_links"] = external[:100]
        result["embedded_parts"] = [name for name in archive.namelist() if "/embeddings/" in name or "vbaProject" in name][:100]
        if extension in {".docx", ".dotx"}:
            body = xml(archive, "word/document.xml")
            names = [name for name in relevant if name in {"word/styles.xml", "word/numbering.xml"} or
                     name.startswith(("word/theme/", "word/header", "word/footer"))]
            roots = [body] + [xml(archive, name) for name in names]
            result.update(observations(roots))
            result["sections"] = [{tag(item): attrs(item) for item in section if tag(item) in {"pgSz", "pgMar", "cols", "titlePg"}}
                                  for section in body.iter() if tag(section) == "sectPr"][:50]
            result["paragraphs"] = [{"text": text(node), "style": next((attrs(item).get("val") for item in node.iter() if tag(item) == "pStyle"), None)}
                                    for node in body.iter() if tag(node) == "p"][:200]
            result["tables"] = [{"rows": sum(tag(item) == "tr" for item in table),
                                  "grid": [attrs(item).get("w") for item in table.iter() if tag(item) == "gridCol"],
                                  "sample": text(table)} for table in body.iter() if tag(table) == "tbl"][:50]
            result["styles"] = [{"attributes": attrs(node), "properties": [{"tag": tag(item), "values": attrs(item)} for item in node.iter() if tag(item) in {"name", "basedOn", "next", "rFonts", "sz", "color", "spacing", "ind", "jc"}]}
                                for root in roots for node in root.iter() if tag(node) == "style"][:100]
            result["reusable_parts"] = names
        else:
            presentation = xml(archive, "ppt/presentation.xml")
            ordered = slide_paths(archive, presentation)
            names = [name for name in relevant if name.startswith(("ppt/theme/", "ppt/slideMasters/", "ppt/slideLayouts/"))]
            slides = [xml(archive, name) for name in ordered[:200]]
            roots = [presentation] + [xml(archive, name) for name in names] + slides
            result.update(observations(roots))
            result["slide_size"] = next((attrs(node) for node in presentation.iter() if tag(node) == "sldSz"), {})
            result["slide_count"] = len(ordered)
            result["analyzed_slide_count"] = len(slides)
            result["slides"] = []
            for index, (name, root) in enumerate(zip(ordered, slides), 1):
                shapes = []
                for shape in root.iter():
                    if tag(shape) in {"sp", "pic", "graphicFrame"}:
                        shapes.append({"type": tag(shape), "text": text(shape),
                            "geometry": [{"tag": tag(item), "values": attrs(item)} for item in shape.iter() if tag(item) in {"off", "ext", "ph"}]})
                result["slides"].append({"number": index, "part": name, "shapes": shapes[:100]})
            result["reusable_parts"] = names
        result["status"] = "needs_visual_review"
    return result
