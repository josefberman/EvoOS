#!/usr/bin/env python3
"""Boot persist ISO with a raw disk, store, kill QEMU, reboot, recall."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


class QMP:
    def __init__(self, path: str, timeout: float = 16.0) -> None:
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

    def sendkey(self, *keys: str) -> None:
        payload = [{"type": "qcode", "data": k} for k in keys]
        r = self.cmd("send-key", keys=payload)
        if "error" in r:
            raise SystemExit(f"send-key {keys}: {r}")


def wait_serial(path: Path, needle: str, timeout: float, start: int = 0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            text = path.read_text(errors="replace")
            if needle in text[start:]:
                return text
        time.sleep(0.05)
    text = path.read_text(errors="replace") if path.exists() else ""
    raise SystemExit(f"timeout waiting for {needle!r}\n--- serial ---\n{text[-4000:]}")


def boot(iso: Path, disk: Path, serial: Path, qmp_path: str) -> subprocess.Popen:
    serial.unlink(missing_ok=True)
    try:
        os.unlink(qmp_path)
    except OSError:
        pass
    qemu = [
        "qemu-system-x86_64",
        "-cdrom",
        str(iso),
        "-drive",
        f"file={disk},if=ide,format=raw,index=0,media=disk",
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
    return subprocess.Popen(qemu, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True)
    ap.add_argument("--disk", required=True)
    ap.add_argument("--serial", required=True)
    args = ap.parse_args()
    iso = Path(args.iso).resolve()
    disk = Path(args.disk).resolve()
    serial = Path(args.serial).resolve()
    qmp_path = str(serial.parent / "qmp-persist.sock")

    proc = boot(iso, disk, serial, qmp_path)
    try:
        q = QMP(qmp_path)
        wait_serial(serial, "EvoOS live", 20.0)
        wait_serial(serial, "device attached", 8.0)
        time.sleep(0.4)
        q.sendkey("w")
        wait_serial(serial, "put=123", 12.0)
        time.sleep(0.3)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    first = serial.read_text(errors="replace") if serial.exists() else ""
    if "put=123" not in first:
        print(first, file=sys.stderr)
        return 1
    if "baseline_persist" not in first and "persist" not in first:
        print("persist ISO should boot persist_rr or an evolved persist elite", file=sys.stderr)
        print(first[-2000:], file=sys.stderr)
        return 1

    proc = boot(iso, disk, serial, qmp_path)
    try:
        q = QMP(qmp_path)
        text = wait_serial(serial, "EvoOS live", 20.0)
        mark = len(text)
        time.sleep(0.4)
        q.sendkey("r")
        wait_serial(serial, "recall=123", 12.0, start=mark)
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
    if "recall=123" not in text:
        print("value did not survive reboot", file=sys.stderr)
        return 1
    print("persist qemu reboot test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
