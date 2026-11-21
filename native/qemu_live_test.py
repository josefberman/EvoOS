#!/usr/bin/env python3
"""Boot evoos.iso, script PS/2 keys via QMP, check serial, screendump."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import zlib
from pathlib import Path


class QMP:
    def __init__(self, path: str, timeout: float = 12.0) -> None:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.s.connect(path)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.1)
        else:
            raise SystemExit(f"qmp connect failed: {last}")
        self.s.settimeout(5)
        self.buf = b""
        self._read_obj()
        self.cmd("qmp_capabilities")

    def _read_obj(self) -> dict:
        while True:
            if b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if not line.strip():
                    continue
                return json.loads(line.decode())
            chunk = self.s.recv(4096)
            if not chunk:
                raise SystemExit("qmp eof")
            self.buf += chunk

    def cmd(self, execute: str, **arguments) -> dict:
        msg: dict = {"execute": execute}
        if arguments:
            msg["arguments"] = arguments
        self.s.sendall((json.dumps(msg) + "\n").encode())
        while True:
            obj = self._read_obj()
            if "return" in obj or "error" in obj:
                return obj
            # event

    def sendkey(self, *keys: str) -> None:
        payload = [{"type": "qcode", "data": k} for k in keys]
        r = self.cmd("send-key", keys=payload)
        if "error" in r:
            raise SystemExit(f"send-key {keys}: {r}")


def wait_serial(path: Path, needle: str, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            text = path.read_text(errors="replace")
            if needle in text:
                return text
        time.sleep(0.05)
    text = path.read_text(errors="replace") if path.exists() else ""
    raise SystemExit(f"timeout waiting for {needle!r}\n--- serial ---\n{text}")


def ppm_to_png(ppm: Path, png: Path) -> None:
    data = ppm.read_bytes()
    if not data.startswith(b"P6"):
        raise SystemExit(f"not a P6 ppm: {ppm}")
    i = 2

    def skip() -> None:
        nonlocal i
        while True:
            while i < len(data) and data[i] in b" \t\r\n":
                i += 1
            if i < len(data) and data[i] == 35:
                while i < len(data) and data[i] != 10:
                    i += 1
                continue
            return

    def tok() -> str:
        nonlocal i
        skip()
        j = i
        while j < len(data) and data[j] not in b" \t\r\n":
            j += 1
        s = data[i:j].decode()
        i = j
        return s

    w = int(tok())
    h = int(tok())
    maxv = int(tok())
    skip()
    raw = data[i:]
    if maxv != 255:
        raise SystemExit("ppm maxval")
    def be32(n: int) -> bytes:
        return n.to_bytes(4, "big")

    def chunk(tag: bytes, body: bytes) -> bytes:
        c = tag + body
        return be32(len(body)) + c + be32(zlib.crc32(c) & 0xFFFFFFFF)

    stride = w * 3
    rows = b"".join(b"\x00" + raw[y * stride : (y + 1) * stride] for y in range(h))
    ihdr = be32(w) + be32(h) + bytes([8, 2, 0, 0, 0])
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--screenshot", required=True)
    ap.add_argument("--uefi", action="store_true")
    ap.add_argument("--ovmf-code", default="/usr/share/OVMF/OVMF_CODE_4M.fd")
    ap.add_argument("--ovmf-vars", default="/usr/share/OVMF/OVMF_VARS_4M.fd")
    args = ap.parse_args()

    iso = Path(args.iso).resolve()
    serial = Path(args.serial).resolve()
    shot = Path(args.screenshot).resolve()
    qmp_path = str(shot.parent / ("qmp-uefi.sock" if args.uefi else "qmp.sock"))
    serial.unlink(missing_ok=True)
    try:
        os.unlink(qmp_path)
    except OSError:
        pass

    qemu = [
        "qemu-system-x86_64",
        "-cdrom",
        str(iso),
        "-m",
        "128M",
        "-display",
        "none",
        "-vga",
        "std",
        "-serial",
        f"file:{serial}",
        "-qmp",
        f"unix:{qmp_path},server,nowait",
        "-device",
        "isa-debug-exit,iobase=0xf4,iosize=0x04",
        "-no-reboot",
        "-cpu",
        "qemu64",
    ]
    tmp_vars = None
    if args.uefi:
        tmp_vars = shot.parent / "ovmf_vars_live.fd"
        tmp_vars.write_bytes(Path(args.ovmf_vars).read_bytes())
        qemu = [
            "qemu-system-x86_64",
            "-drive",
            f"if=pflash,format=raw,readonly=on,file={args.ovmf_code}",
            "-drive",
            f"if=pflash,format=raw,file={tmp_vars}",
        ] + qemu[1:]

    proc = subprocess.Popen(qemu, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        q = QMP(qmp_path, timeout=20.0 if args.uefi else 12.0)
        wait_serial(serial, "EvoOS live", 20.0)
        time.sleep(0.4)
        q.sendkey("h")
        wait_serial(serial, "keys:", 8.0)
        q.sendkey("m")
        wait_serial(serial, "malicious", 8.0)
        q.sendkey("s")
        wait_serial(serial, "loader:", 8.0)
        q.sendkey("i")
        wait_serial(serial, "inspect isolation_n=", 8.0)
        time.sleep(0.5)
        ppm = shot.with_suffix(".ppm")
        r = q.cmd("screendump", filename=str(ppm))
        if "error" in r:
            print("screendump error", r, file=sys.stderr)
        elif ppm.exists() and ppm.stat().st_size > 100:
            try:
                ppm_to_png(ppm, shot)
                print(f"wrote {shot} ({shot.stat().st_size} bytes)")
            except Exception as exc:
                print(f"ppm_to_png failed: {exc}", file=sys.stderr)
        time.sleep(0.2)
        q.sendkey("q")
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    text = serial.read_text(errors="replace") if serial.exists() else ""
    print(text)
    if "EvoOS live" not in text:
        return 1
    if "malicious" not in text:
        return 1
    if "loader:" not in text:
        return 1
    if "inspect isolation_n=" not in text:
        return 1
    if "EvoOS quit" not in text:
        return 1
    # QEMU debug-exit 0 -> status 1
    if proc.returncode not in (0, 1):
        print(f"unexpected qemu status {proc.returncode}", file=sys.stderr)
        return 1
    print("live qemu test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
