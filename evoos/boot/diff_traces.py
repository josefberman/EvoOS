"""Run the same EVS0 image in Python and the hosted C interpreter; compare traces."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from evoos.vm.codec import format_trace, loads_image


def python_trace(image: Path) -> str:
    m, _meta = loads_image(image.read_bytes())
    m.run()
    return format_trace(m)


def native_trace(host: Path, image: Path) -> str:
    r = subprocess.run(
        [str(host), str(image)],
        check=True,
        capture_output=True,
        text=True,
    )
    return r.stdout


def first_diff(a: str, b: str) -> str:
    al, bl = a.splitlines(), b.splitlines()
    n = max(len(al), len(bl))
    for i in range(n):
        la = al[i] if i < len(al) else "<missing>"
        lb = bl[i] if i < len(bl) else "<missing>"
        if la != lb:
            return f"line {i + 1}\n  py: {la}\n  c:  {lb}"
    return "traces differ (whitespace?)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Differential Python vs C VM traces")
    p.add_argument("--host", required=True)
    p.add_argument("--image", required=True)
    args = p.parse_args(argv)
    image = Path(args.image)
    host = Path(args.host)
    py = python_trace(image)
    c = native_trace(host, image)
    if py == c:
        print(f"OK {image} ({py.count(chr(10))} lines)")
        return 0
    print(first_diff(py, c), file=sys.stderr)
    Path("/tmp/evoos_py.trace").write_text(py)
    Path("/tmp/evoos_c.trace").write_text(c)
    print("wrote /tmp/evoos_py.trace and /tmp/evoos_c.trace", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
