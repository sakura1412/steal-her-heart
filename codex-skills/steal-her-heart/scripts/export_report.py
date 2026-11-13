"""Export a checked editorial plan to a searchable PDF or editable PPTX and a draft map."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from reportlab.lib import colors
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.textsplit import wordSplit

from report_text import pages
from run_files import digest, load, read_full, read_quick, require, validate


def resolve_sections(root, plan):
    snapshots = [p for p in (root / "manifest.json", root / "quick.json") if p.exists()]
    require(len(snapshots) == 1, "expected one locked snapshot")
    snapshot = snapshots[0]
    require(digest(snapshot) == plan["input_manifest_sha256"], "snapshot hash mismatch")
    run = load(snapshot)
    quick = snapshot.name == "quick.json"
    rows, _, _, _ = read_quick(run) if quick else read_full(root, run)
    index = {(r["record_type"], r["record_id"], r["revision"]): r["data"] for r in rows}

    def find(kind, ref):
        return index[kind, ref["record_id"], ref["revision"]]

    sections, citations = [], {}
    for section in plan["sections"]:
        blocks = []
        for block in section["blocks"]:
            kind = block["kind"]
            require(kind in ("fact", "analysis", "limitation"), "unknown block kind")
            refs = block.get("claim_refs", [])
            require(kind == "limitation" or refs, "conclusion missing claim_refs")
            texts = []
            for ref in refs:
                claim = find("claim", ref)
                require(claim["status"] == "supported" and claim["supporting_evidence"], "unsupported claim")
                texts.append(claim["text"])
                for evidence_ref in claim["supporting_evidence"]:
                    evidence = find("evidence", evidence_ref)
                    require(evidence["verification_status"] == "verified", "unverified evidence")
                    source = find("source", evidence["source_ref"])
                    label = f'{evidence_ref["record_id"]} · {source["url"]} · {source["collected_at"]}'
                    citations.setdefault(label, []).append(ref)
            text = block.get("text", " ".join(texts))
            require(isinstance(text, str) and text.strip(), "empty report block")
            blocks.append((kind, text, refs))
        require(section["title"].strip() and blocks, "empty section")
        sections.append((section["title"], blocks))
    require(sections, "empty report")
    quality = run["quality"] if quick else run["qa"]["status"]
    if quality in ("limited", "insufficient"):
        gaps = run["gaps"] if quick else [run["qa"]["reason"]]
        sections.insert(0, ("研究范围与限制", [("limitation", text, []) for text in gaps]))
    if citations and not plan.get("presentation_copy", False):
        sections.append(("证据与来源", [("fact", text, refs) for text, refs in citations.items()]))
    return run["snapshot_id"], sections


def wrap(text, size, width):
    lines = []
    for paragraph in text.splitlines():
        for line in simpleSplit(paragraph, "Body", size, width):
            if pdfmetrics.stringWidth(line, "Body", size) <= width:
                lines.append(line)
            else:
                lines.extend(s.strip() for _, s in wordSplit(line, width, "Body", size))
    return [s for s in lines if s.strip()]


def layout(sections, fmt):
    width, height, body, title = (595, 842, 13, 22) if fmt == "pdf" else (960, 540, 19, 30)
    result = []
    for heading, blocks in sections:
        headings = wrap(heading, title, width - 108)
        start = 44 + len(headings) * title * 1.5 + 28
        require(start < height - 90, "shorten section title")
        def new_page():
            page = [(line, 48, 38 + i * title * 1.5, title, "heading", []) for i, line in enumerate(headings)]
            result.append(page)
            return page
        page = new_page()
        y = start
        for kind, text, refs in blocks:
            for line in wrap(text, body, width - 108):
                if y + body * 1.6 > height - 40:
                    page, y = new_page(), start
                page.append((line, 48, y, body, kind, refs))
                y += body * 1.6
            y += body * .8
    return width, height, result


def render_pdf(path, width, height, sheets):
    canvas = Canvas(str(path), pagesize=(width, height))
    for sheet in sheets:
        for text, x, top, size, kind, _ in sheet:
            canvas.setFont("Body", size)
            canvas.setFillColor(colors.HexColor("#12675D" if kind == "heading" else "#203342"))
            canvas.drawString(x, height - top - size, text)
        canvas.showPage()
    canvas.save()


def render_pptx(path, width, height, sheets, family):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.oxml.xmlchemy import OxmlElement
    from pptx.util import Pt
    deck = Presentation()
    deck.slide_width, deck.slide_height = Pt(width), Pt(height)
    for sheet in sheets:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for text, x, top, size, kind, _ in sheet:
            frame = slide.shapes.add_textbox(Pt(x), Pt(top), Pt(width-x-36), Pt(size*1.6)).text_frame
            frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
            frame.word_wrap = False
            run = frame.paragraphs[0].add_run()
            run.text = text
            run.font.name, run.font.size = family, Pt(size)
            run.font.color.rgb = RGBColor.from_string("12675D" if kind == "heading" else "203342")
            east_asian = OxmlElement("a:ea")
            east_asian.set("typeface", family)
            run._r.get_or_add_rPr().append(east_asian)
    deck.save(path)


def export(root, plan, output, font):
    root, output = root.resolve(), output.resolve()
    fmt = output.suffix[1:].lower()
    require(fmt in ("pdf", "pptx") and root in output.parents, "expected PDF/PPTX path inside run")
    snapshot_id, sections = resolve_sections(root, plan)
    pdfmetrics.registerFont(TTFont("Body", str(font)))
    family = pdfmetrics.getFont("Body").face.familyName
    if isinstance(family, bytes):
        family = family.decode()
    width, height, sheets = layout(sections, fmt)
    output.parent.mkdir(parents=True, exist_ok=True)
    sidecar = output.with_suffix(output.suffix + ".draft.json")
    with TemporaryDirectory(dir=output.parent) as tmp:
        file = Path(tmp) / output.name
        if fmt == "pdf":
            render_pdf(file, width, height, sheets)
        else:
            render_pptx(file, width, height, sheets, family)
        expected = [[row[0] for row in sheet] for sheet in sheets]
        require(pages(file, fmt) == expected, "extracted text differs from layout; check font and draft")
        paragraphs = []
        for page, sheet in enumerate(sheets, 1):
            for line, (_, _, _, _, kind, refs) in enumerate(sheet, 1):
                paragraphs.append(dict(kind=kind, claim_refs=refs, location=f"p{page}/{line}",
                                       text_ranges=[[page, line, line]]))
        checks = dict.fromkeys(("content", "render", "citations", "cross_format_consistency"), "not_run")
        data = dict(format=fmt, artifact_path=str(output.relative_to(root)), artifact_sha256=digest(file),
                    input_snapshot_id=snapshot_id, input_manifest_sha256=plan["input_manifest_sha256"],
                    status="draft", paragraphs=paragraphs, checks=checks, failure_reason=None)
        row = dict(schema_version="1.2.0", record_type="report", record_id=f"report-{uuid4().hex}",
                   recorded_at=datetime.now(timezone.utc).isoformat(), revision=1, supersedes=None, data=data)
        validate("ledger-record", row)
        draft = Path(tmp) / "map.json"
        draft.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        os.link(file, output)
        try:
            os.link(draft, sidecar)
        except OSError:
            output.unlink()
            raise
    return sidecar


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", help="relative path inside the run")
    parser.add_argument("--font", required=True, type=Path, help="readable TrueType font covering the report language")
    args = parser.parse_args()
    try:
        print(export(args.run, load(args.plan), args.run / args.output, args.font))
    except (KeyError, ValueError, OSError) as error:
        sys.exit(f"Export failed: {error}")
