"""Enumerate nonblank text lines in export order and check paragraph coverage."""
import posixpath
from collections import Counter
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from run_files import require


def slide_text(path):
    ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    with ZipFile(path) as archive:
        rels = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        for slide in presentation.findall("p:sldIdLst/p:sldId", ns):
            target = targets[slide.get("{%s}id" % ns["r"])]
            name = posixpath.normpath(posixpath.join("ppt", target)).lstrip("/")
            tree = ET.fromstring(archive.read(name))
            yield "\n".join(node.text or "" for node in tree.findall(".//a:t", ns))


def pages(path, fmt):
    if fmt == "pdf":
        from pypdf import PdfReader
        contents = (page.extract_text() for page in PdfReader(path).pages)
    elif fmt == "pptx":
        contents = slide_text(path)
    else:
        contents = [path.read_text(encoding="utf-8")]
    return [[line.strip() for line in page.splitlines() if line.strip()] for page in contents]


def check_text_map(path, report):
    content = pages(path, report["format"])
    require(content and all(content), "page without extractable text: inspect export")
    counts = Counter()
    for paragraph in report["paragraphs"]:
        for page, first, last in paragraph["text_ranges"]:
            require(1 <= page <= len(content) and 1 <= first <= last <= len(content[page-1]),
                    f"invalid report text range: {page}/{first}-{last}")
            counts.update((page, line) for line in range(first, last + 1))
    for page, lines in enumerate(content, 1):
        for line in range(1, len(lines) + 1):
            require(counts[page, line] == 1, f"unmapped or overlapping report text: page {page}, line {line}")
