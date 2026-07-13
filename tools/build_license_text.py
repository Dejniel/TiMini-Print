from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from timiniprint.licensing import write_license_text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build one platform-specific license text for a frozen executable."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_license_text(
        args.output,
        additional_distribution_names=("PyInstaller",),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
