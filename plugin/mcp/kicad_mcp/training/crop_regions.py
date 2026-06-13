# SPDX-License-Identifier: GPL-3.0-or-later
"""
Phase 1.3: Crop schematic/PCB regions from PDF pages.

Uses bounding boxes from detect_content to isolate the relevant region,
removing surrounding article text.

Usage:
    python -m kicad_mcp.training.crop_regions --classification /tmp/elektor_training/content_classification.json
"""

import json
import logging
from pathlib import Path

import pymupdf  # pylint: disable=import-error  # optional training dep

logger = logging.getLogger(__name__)

# Padding around detected bounding box (in points)
CROP_PADDING = 15.0
# Minimum region size to be considered valid (in points)
MIN_REGION_SIZE = 100.0
# DPI for cropped output images
CROP_DPI = 200


def crop_page_region(
    pdf_path: str | Path,
    page_num: int,
    bbox: tuple[float, float, float, float],
    output_path: str | Path,
    dpi: int = CROP_DPI,
    padding: float = CROP_PADDING,
) -> dict | None:
    """Crop a region from a PDF page and save as PNG.

    Args:
        pdf_path: Path to the PDF file
        page_num: 1-based page number
        bbox: Bounding box (x0, y0, x1, y1) in PDF points
        output_path: Where to save the cropped image
        dpi: Output resolution
        padding: Extra padding around bbox (points)

    Returns:
        Metadata dict or None if region too small.
    """
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_num - 1]

    # Expand bbox with padding and clamp to page bounds
    x0 = max(0, bbox[0] - padding)
    y0 = max(0, bbox[1] - padding)
    x1 = min(page.rect.width, bbox[2] + padding)
    y1 = min(page.rect.height, bbox[3] + padding)

    # Validate region size
    region_w = x1 - x0
    region_h = y1 - y0
    if region_w < MIN_REGION_SIZE or region_h < MIN_REGION_SIZE:
        doc.close()
        return None

    clip = pymupdf.Rect(x0, y0, x1, y1)
    pix = page.get_pixmap(dpi=dpi, clip=clip)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(output_path))
    doc.close()

    return {
        "output_path": str(output_path),
        "bbox_pt": (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)),
        "size_px": (pix.width, pix.height),
        "region_pt": (round(region_w, 1), round(region_h, 1)),
    }


def crop_classified_pages(
    classification_path: str | Path,
    archive_dir: str | Path,
    output_dir: str | Path,
    min_confidence: float = 0.4,
    content_types: tuple[str, ...] = ("schematic", "mixed"),
) -> list[dict]:
    """Crop schematic regions from all classified pages.

    Args:
        classification_path: Path to content_classification.json
        archive_dir: Path to articles/ directory (to resolve PDF paths)
        output_dir: Where to save cropped images
        min_confidence: Minimum classification confidence
        content_types: Which content types to crop

    Returns:
        List of crop result dicts.
    """
    with open(classification_path, encoding="utf-8") as f:
        classification = json.load(f)

    output_dir = Path(output_dir)
    crops_dir = output_dir / "crops"
    results = []

    for pdf_cls in classification["classifications"]:
        source_path = None
        # Find the source PDF — try year-based path
        year = pdf_cls.get("year")
        if year:
            candidate = Path(archive_dir) / str(year) / pdf_cls["filename"]
            if candidate.exists():
                source_path = candidate

        if not source_path:
            logger.warning("PDF not found: %s", pdf_cls["filename"])
            continue

        for page_cls in pdf_cls["pages"]:
            if page_cls["content_type"] not in content_types:
                continue
            if page_cls["confidence"] < min_confidence:
                continue
            if not page_cls.get("schematic_bbox"):
                continue

            bbox = tuple(page_cls["schematic_bbox"])
            stem = pdf_cls["stem"]
            page_num = page_cls["page"]
            crop_name = f"{stem}_p{page_num}_crop.png"
            crop_path = crops_dir / str(year or "unknown") / crop_name

            try:
                result = crop_page_region(
                    pdf_path=source_path,
                    page_num=page_num,
                    bbox=bbox,
                    output_path=crop_path,
                )
                if result:
                    result.update({
                        "filename": pdf_cls["filename"],
                        "stem": stem,
                        "year": year,
                        "page": page_num,
                        "content_type": page_cls["content_type"],
                        "confidence": page_cls["confidence"],
                    })
                    results.append(result)
            except Exception as e:
                logger.error("Failed to crop %s p%d: %s", pdf_cls["filename"], page_num, e)

    # Save crop index
    index_path = output_dir / "crop_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_crops": len(results),
            "crops": results,
        }, f, indent=2, ensure_ascii=False)

    logger.info("Cropped %d schematic regions -> %s", len(results), index_path)
    return results


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Crop schematic regions from classified PDFs")
    parser.add_argument("--classification", required=True, help="Path to content_classification.json")
    parser.add_argument("--archive-dir", required=True, help="Path to articles/ directory")
    parser.add_argument("--output-dir", default="/tmp/elektor_training", help="Output directory")
    parser.add_argument("--min-confidence", type=float, default=0.4)
    args = parser.parse_args()

    crop_classified_pages(
        args.classification,
        args.archive_dir,
        args.output_dir,
        min_confidence=args.min_confidence,
    )
