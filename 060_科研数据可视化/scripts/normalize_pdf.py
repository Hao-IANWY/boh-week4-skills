#!/usr/bin/env python3
"""Pin volatile PDF dates without changing pages, fonts or plotted content.

Cairo/pypdf write wall-clock dates. Pinning them to the Unix epoch makes the
end-to-end hash a reproducibility check instead of a clock comparison.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def normalize(path: Path) -> None:
    reader = PdfReader(str(path))
    writer = PdfWriter(clone_from=reader)
    metadata = dict(reader.metadata or {})
    # pypdf otherwise inserts the current time when writing; pin both fields.
    metadata["/CreationDate"] = "D:19700101000000+00'00'"
    metadata["/ModDate"] = "D:19700101000000+00'00'"
    writer.add_metadata(metadata)
    temporary = path.with_name(path.name + ".normalized.tmp")
    with temporary.open("wb") as handle:
        writer.write(handle)
    if not PdfReader(str(temporary)).pages:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"normalized PDF has no pages: {path.name}")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="run_plan.json")
    parser.add_argument("--out", default=".")
    args = parser.parse_args()
    out_dir = Path(args.out).resolve()
    plan = json.loads((out_dir / args.plan).read_text(encoding="utf-8"))
    normalized = []
    for figure in plan.get("figures", []):
        path = out_dir / f"figure_{figure['index']}.pdf"
        if path.exists():
            normalize(path)
            normalized.append(path.name)
    print(json.dumps({"ok": True, "normalized": normalized}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
