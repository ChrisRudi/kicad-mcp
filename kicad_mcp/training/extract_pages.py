# SPDX-License-Identifier: GPL-3.0-or-later
"""
Phase 1.1: Extract pages from Elektor PDFs as PNG images.

Usage:
    python -m kicad_mcp.training.extract_pages --archive-dir /path/to/articles --output-dir /tmp/elektor_training
    python -m kicad_mcp.training.extract_pages --archive-dir /path/to/articles --years 2005-2010 --max-pdfs 50
"""

import json
import logging
from pathlib import Path

import pymupdf  # pylint: disable=import-error  # optional training dep

logger = logging.getLogger(__name__)

DEFAULT_DPI = 150
DEFAULT_YEARS = range(2005, 2015)  # 2005-2014: best quality, consistent layout


def extract_pdf_pages(
    pdf_path: str | Path,
    output_dir: str | Path,
    dpi: int = DEFAULT_DPI,
) -> dict:
    """Extract all pages from a single PDF as PNG images.

    Returns metadata dict with filename, year, page count, and per-page info.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = pdf_path.stem
    doc = pymupdf.open(str(pdf_path))

    pages_info = []
    for i in range(len(doc)):
        page = doc[i]
        pix = page.get_pixmap(dpi=dpi)
        img_name = f"{stem}_p{i + 1}.png"
        img_path = output_dir / img_name
        pix.save(str(img_path))
        pages_info.append({
            "page": i + 1,
            "image": img_name,
            "width_px": pix.width,
            "height_px": pix.height,
            "width_pt": round(page.rect.width, 1),
            "height_pt": round(page.rect.height, 1),
        })

    doc.close()

    metadata = {
        "filename": pdf_path.name,
        "stem": stem,
        "page_count": len(pages_info),
        "pages": pages_info,
    }
    return metadata


def extract_archive(
    archive_dir: str | Path,
    output_dir: str | Path,
    years: range | list[int] | None = None,
    max_pdfs: int | None = None,
    dpi: int = DEFAULT_DPI,
) -> list[dict]:
    """Batch-extract pages from Elektor PDF archive.

    Args:
        archive_dir: Path to articles/ directory (contains year subdirs)
        output_dir: Where to save extracted images
        years: Which years to process (default: 2005-2014)
        max_pdfs: Max PDFs to process (None = all)
        dpi: Resolution for extracted images

    Returns:
        List of metadata dicts, one per PDF.
    """
    archive_dir = Path(archive_dir)
    output_dir = Path(output_dir)
    years = years or DEFAULT_YEARS

    all_metadata = []
    pdf_count = 0

    for year in sorted(years):
        year_dir = archive_dir / str(year)
        if not year_dir.is_dir():
            logger.warning("Year directory not found: %s", year_dir)
            continue

        year_output = output_dir / "pages" / str(year)
        pdfs = sorted(year_dir.glob("*.pdf"))
        logger.info("Year %d: %d PDFs found", year, len(pdfs))

        for pdf_path in pdfs:
            if max_pdfs and pdf_count >= max_pdfs:
                break

            try:
                meta = extract_pdf_pages(pdf_path, year_output, dpi=dpi)
                meta["year"] = year
                meta["source_path"] = str(pdf_path)
                all_metadata.append(meta)
                pdf_count += 1

                if pdf_count % 50 == 0:
                    logger.info("Processed %d PDFs...", pdf_count)

            except Exception as e:
                logger.error("Failed to extract %s: %s", pdf_path.name, e)

        if max_pdfs and pdf_count >= max_pdfs:
            break

    # Save index
    index_path = output_dir / "extraction_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_pdfs": len(all_metadata),
            "total_pages": sum(m["page_count"] for m in all_metadata),
            "years": sorted(set(m["year"] for m in all_metadata)),
            "pdfs": all_metadata,
        }, f, indent=2, ensure_ascii=False)

    logger.info(
        "Extraction complete: %d PDFs, %d pages -> %s",
        len(all_metadata),
        sum(m["page_count"] for m in all_metadata),
        index_path,
    )
    return all_metadata


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Extract Elektor PDF pages as images")
    parser.add_argument("--archive-dir", required=True, help="Path to articles/ directory")
    parser.add_argument("--output-dir", default="/tmp/elektor_training", help="Output directory")
    parser.add_argument("--years", default="2005-2014", help="Year range (e.g. 2005-2014)")
    parser.add_argument("--max-pdfs", type=int, default=None, help="Max PDFs to process")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="Image DPI")

    args = parser.parse_args()

    # Parse year range
    if "-" in args.years:
        start, end = args.years.split("-")
        year_range: range | list[int] = range(int(start), int(end) + 1)
    else:
        year_range = [int(y) for y in args.years.split(",")]

    extract_archive(
        archive_dir=args.archive_dir,
        output_dir=args.output_dir,
        years=year_range,
        max_pdfs=args.max_pdfs,
        dpi=args.dpi,
    )
