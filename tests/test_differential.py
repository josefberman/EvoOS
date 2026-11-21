"""Python VM vs hosted C interpreter on the same EVS0 image."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from evoos.boot.diff_traces import native_trace, python_trace
from evoos.boot.pack import pack_workload, select_supervisor
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.vm.codec import dumps_image, format_trace
from evoos.vm.instruction_set import assemble
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import load_firmware

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"
BUILD = ROOT / "build"


def _build_host() -> Path:
    BUILD.mkdir(exist_ok=True)
    host = BUILD / "evoos_host"
    subprocess.run(
        ["gcc", "-O2", "-std=c11", "-Wall", "-o", str(host), "evoos_vm.c", "evoos_host.c"],
        cwd=NATIVE,
        check=True,
    )
    return host


@pytest.fixture(scope="module")
def host() -> Path:
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    return _build_host()


def _compare(host: Path, blob: bytes, name: str) -> None:
    img = BUILD / f"{name}.img"
    BUILD.mkdir(exist_ok=True)
    img.write_bytes(blob)
    py = python_trace(img)
    c = native_trace(host, img)
    if py != c:
        Path("/tmp/evoos_py.trace").write_text(py)
        Path("/tmp/evoos_c.trace").write_text(c)
    assert py == c, name


def test_diff_opcode_smoke(host: Path):
    src = """
        MOVI r1, 0
        MOVI r2, 10
    loop:
        BGE r1, r2, done
        ADDI r1, r1, 1
        JMP loop
    done:
        HALT
    """
    m = Machine(MachineConfig(max_steps=1000, n_contexts=1))
    m.install_supervisor(assemble(src))
    _compare(host, dumps_image(m), "loop")


def test_diff_alu_and_memory(host: Path):
    src = """
        MOVI r1, 100
        MOVI r2, 7
        ADD r3, r1, r2
        SUB r4, r1, r2
        MUL r5, r2, r2
        STORE r1, r3, 0
        LOAD r6, r1, 0
        SHL r7, r2, r0
        MOVI r1, 3
        SHR r1, r5, r1
        XOR r2, r3, r4
        AND r3, r3, r4
        OR r4, r4, r2
        NOT r5, r5
        CMP r6, r6, r2
        HALT
    """
    m = Machine(MachineConfig(max_steps=1000, n_contexts=1))
    m.install_supervisor(assemble(src))
    _compare(host, dumps_image(m), "alu")


def test_diff_evolved_train_small(host: Path):
    instrs, origin = select_supervisor("evolved")
    blob = pack_workload(instrs, training_workloads()[0], origin=origin)
    _compare(host, blob, "evolved_train_small")


def test_diff_rr_heldout(host: Path):
    instrs, origin = select_supervisor("rr")
    blob = pack_workload(instrs, heldout_workloads()[0], origin=origin)
    _compare(host, blob, "rr_heldout_mixed")


def test_diff_blk_svc_roundtrip(host: Path):
    src = """
        MOVI r1, 256
        MOVI r2, 42
        STORE r1, r2, 0
        MOVI r3, 3
        BLKWR r3, r1
        MOVI r4, 384
        BLKRD r5, r3, r4
        LOAD r6, r4, 0
        HALT
    """
    m = Machine(MachineConfig(max_steps=1000, n_contexts=1))
    m.install_supervisor(assemble(src))
    _compare(host, dumps_image(m), "blk_svc")


def test_diff_persist_rr_train_small(host: Path):
    instrs, origin = select_supervisor("persist_rr")
    blob = pack_workload(instrs, training_workloads()[0], origin=origin)
    _compare(host, blob, "persist_rr_train_small")


def test_python_format_trace_stable():
    instrs, origin = select_supervisor("rr")
    cfg = MachineConfig()
    m = Machine(cfg)
    load_firmware(m, training_workloads()[0])
    m.install_supervisor(instrs)
    m.run()
    t = format_trace(m)
    assert t.startswith("# evoos-trace v1\n")
    assert "completed" not in t
    assert sum(1 for line in t.splitlines() if line.startswith("ctx ")) == cfg.n_contexts
