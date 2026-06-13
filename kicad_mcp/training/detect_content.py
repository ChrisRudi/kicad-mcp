# SPDX-License-Identifier: GPL-3.0-or-later
"""
Phase 1.2: Detect page content type (schematic, PCB, text/photo).

Uses pymupdf vector graphics analysis to classify pages without ML.
For modern PDFs where schematics are embedded as images, pages are
tagged as 'image_content' for later Claude Vision classification.

Usage:
    python -m kicad_mcp.training.detect_content --index /tmp/elektor_training/extraction_index.json
"""

from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path

import pymupdf  # pylint: disable=import-error  # optional training dep

logger = logging.getLogger(__name__)


@dataclass
class PageClassification:
    page: int
    content_type: str  # 'schematic', 'pcb_drawing', 'image_content', 'text', 'mixed'
    confidence: float  # 0.0-1.0
    h_lines: int
    v_lines: int
    curves: int
    total_items: int
    text_chars: int
    image_count: int
    schematic_bbox: tuple[float, float, float, float] | None  # (x0, y0, x1, y1) in pts


def classify_page(page: pymupdf.Page) -> PageClassification:
    """Classify a single PDF page by analyzing its vector content."""
    drawings = page.get_drawings()
    text_chars = len(page.get_text())
    images = page.get_images()

    h_lines, v_lines, curves, total_items = 0, 0, 0, 0
    line_points_x, line_points_y = [], []

    for d in drawings:
        for item in d.get("items", []):
            total_items += 1
            if item[0] == "l":  # line segment
                p1, p2 = item[1], item[2]
                dx = abs(p1.x - p2.x)
                dy = abs(p1.y - p2.y)
                if dy < 2 and dx > 5:
                    h_lines += 1
                    line_points_x.extend([p1.x, p2.x])
                    line_points_y.extend([p1.y, p2.y])
                elif dx < 2 and dy > 5:
                    v_lines += 1
                    line_points_x.extend([p1.x, p2.x])
                    line_points_y.extend([p1.y, p2.y])
            elif item[0] == "c":  # curve
                curves += 1

    page_w, page_h = page.rect.width, page.rect.height

    # Compute bounding box of line region
    schematic_bbox = None
    if line_points_x and line_points_y:
        schematic_bbox = (
            min(line_points_x),
            min(line_points_y),
            max(line_points_x),
            max(line_points_y),
        )

    # Classification heuristics
    content_type, confidence = _classify_heuristic(
        h_lines=h_lines,
        v_lines=v_lines,
        curves=curves,
        total_items=total_items,
        text_chars=text_chars,
        image_count=len(images),
        page_w=page_w,
        page_h=page_h,
        bbox=schematic_bbox,
    )

    return PageClassification(
        page=page.number + 1,
        content_type=content_type,
        confidence=confidence,
        h_lines=h_lines,
        v_lines=v_lines,
        curves=curves,
        total_items=total_items,
        text_chars=text_chars,
        image_count=len(images),
        schematic_bbox=schematic_bbox,
    )


def _classify_heuristic(
    *,
    h_lines: int,
    v_lines: int,
    curves: int,
    total_items: int,
    text_chars: int,
    image_count: int,
    page_w: float,
    page_h: float,
    bbox: tuple[float, float, float, float] | None,
) -> tuple[str, float]:
    """Apply heuristic rules to classify page content.

    Returns (content_type, confidence).
    """
    ortho_lines = h_lines + v_lines

    # Rule 1: Almost no vector content — likely photo/text page or image-embedded schematic
    if total_items < 50:
        if image_count > 0:
            return ("image_content", 0.6)
        return ("text", 0.8)

    # Rule 2: High orthogonal line density with balanced h/v ratio → schematic
    if ortho_lines > 80:
        hv_balance = min(h_lines, v_lines) / max(h_lines, v_lines, 1)
        curve_ratio = curves / max(ortho_lines, 1)

        # Schematics: balanced h/v lines, moderate curves (component symbols)
        if hv_balance > 0.3 and curve_ratio < 3.0:
            # Check if schematic covers significant page area
            if bbox:
                bbox_w = bbox[2] - bbox[0]
                bbox_h = bbox[3] - bbox[1]
                area_ratio = (bbox_w * bbox_h) / (page_w * page_h)
                if area_ratio > 0.15:
                    confidence = min(0.95, 0.5 + hv_balance * 0.3 + min(area_ratio, 0.5))
                    return ("schematic", confidence)

            confidence = min(0.85, 0.4 + hv_balance * 0.3)
            return ("schematic", confidence)

    # Rule 3: Very high item count with many curves → PCB rendering or complex drawing
    if total_items > 5000 and curves > 1000:
        if image_count > 0:
            return ("pcb_drawing", 0.5)
        return ("pcb_drawing", 0.6)

    # Rule 4: Many images with some vector content → mixed (photo page with annotations)
    if image_count >= 3 and total_items > 100:
        return ("image_content", 0.7)

    # Rule 5: Moderate vector content with text → mixed page
    if total_items > 200 and text_chars > 1500:
        # Could be schematic embedded in article text
        if ortho_lines > 40 and bbox:
            bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            page_area = page_w * page_h
            if bbox_area / page_area > 0.1:
                return ("mixed", 0.5)  # needs Vision to confirm
        return ("text", 0.5)

    # Rule 6: Moderate ortho lines, not enough for confident schematic
    if ortho_lines > 30:
        return ("mixed", 0.4)

    # Default: text with some graphics
    return ("text", 0.4)


def classify_pdf(pdf_path: str | Path) -> list[PageClassification]:
    """Classify all pages of a PDF."""
    doc = pymupdf.open(str(pdf_path))
    results = []
    for i in range(len(doc)):
        page = doc[i]
        result = classify_page(page)
        results.append(result)
    doc.close()
    return results


def classify_archive(
    extraction_index_path: str | Path,
    output_path: str | Path | None = None,
) -> dict:
    """Classify all PDFs referenced in an extraction index.

    Args:
        extraction_index_path: Path to extraction_index.json from extract_pages
        output_path: Where to save classification results (default: alongside index)

    Returns:
        Classification summary dict.
    """
    index_path = Path(extraction_index_path)
    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    if output_path is None:
        output_path = index_path.parent / "content_classification.json"
    output_path = Path(output_path)

    all_classifications = []
    type_counts = {"schematic": 0, "pcb_drawing": 0, "image_content": 0, "text": 0, "mixed": 0}

    for pdf_meta in index["pdfs"]:
        source_path = pdf_meta["source_path"]
        try:
            page_classes = classify_pdf(source_path)
            pdf_result = {
                "filename": pdf_meta["filename"],
                "stem": pdf_meta["stem"],
                "year": pdf_meta.get("year"),
                "pages": [asdict(pc) for pc in page_classes],
            }
            all_classifications.append(pdf_result)

            for pc in page_classes:
                type_counts[pc.content_type] = type_counts.get(pc.content_type, 0) + 1

        except Exception as e:
            logger.error("Failed to classify %s: %s", source_path, e)

    # Collect schematic pages for easy access
    schematic_pages = []
    for pdf_result in all_classifications:
        for page_cls in pdf_result["pages"]:
            if page_cls["content_type"] in ("schematic", "mixed") and page_cls["confidence"] >= 0.4:
                schematic_pages.append({
                    "filename": pdf_result["filename"],
                    "stem": pdf_result["stem"],
                    "year": pdf_result.get("year"),
                    "page": page_cls["page"],
                    "confidence": page_cls["confidence"],
                    "content_type": page_cls["content_type"],
                    "bbox": page_cls.get("schematic_bbox"),
                })

    result = {
        "total_pages": sum(len(p["pages"]) for p in all_classifications),
        "type_counts": type_counts,
        "schematic_pages": len(schematic_pages),
        "classifications": all_classifications,
        "schematic_index": schematic_pages,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info(
        "Classification complete: %d pages — schematics=%d, pcb=%d, images=%d, text=%d, mixed=%d",
        result["total_pages"],
        type_counts.get("schematic", 0),
        type_counts.get("pcb_drawing", 0),
        type_counts.get("image_content", 0),
        type_counts.get("text", 0),
        type_counts.get("mixed", 0),
    )
    return result


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Classify Elektor PDF pages")
    parser.add_argument("--index", required=True, help="Path to extraction_index.json")
    parser.add_argument("--output", default=None, help="Output path for classification JSON")
    args = parser.parse_args()

    classify_archive(args.index, args.output)
