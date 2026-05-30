#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Iterable


PINNED = {
    # Verified with current parser code (PaddleOCR 3.x API).
    "paddleocr": ["paddleocr==3.6.0"],
    # PDF image OCR needs renderer + OCR engine.
    "pdf_ocr": ["pypdfium2>=4.30,<5", "paddleocr==3.6.0"],
    # Fixed to the currently validated Whisper package version.
    "whisper": ["openai-whisper==20250625"],
}


def _run_pip(packages: Iterable[str], upgrade: bool = False) -> int:
    pkgs = [p for p in packages if p]
    if not pkgs:
        return 0
    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    cmd.extend(pkgs)
    print("[install-parse-backends] >", " ".join(cmd))
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install pinned parsing backends for SJTU Agent")
    parser.add_argument(
        "--backend",
        choices=["all", "paddleocr", "pdf_ocr", "whisper"],
        default="all",
        help="backend group to install",
    )
    parser.add_argument("--upgrade", action="store_true", help="pass --upgrade to pip")
    args = parser.parse_args(argv)

    if args.backend == "all":
        targets = ["pdf_ocr", "whisper"]
    else:
        targets = [args.backend]

    merged: list[str] = []
    for t in targets:
        for pkg in PINNED[t]:
            if pkg not in merged:
                merged.append(pkg)

    print(f"[install-parse-backends] Python: {sys.version.split()[0]}")
    print(f"[install-parse-backends] Targets: {', '.join(targets)}")
    print(f"[install-parse-backends] Packages: {', '.join(merged)}")

    rc = _run_pip(merged, upgrade=args.upgrade)
    if rc == 0:
        print("[install-parse-backends] done")
    else:
        print(f"[install-parse-backends] failed with code={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
