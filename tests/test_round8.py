"""Round 8: routing curriculum, shared-window isolation, embed format."""

from __future__ import annotations

from evoos.evaluator.cases import case_vector
from evoos.evaluator.hw_workloads import route_workload, routing_stages
from evoos.experiments.scheduling.hw_baselines import fifo_swap_rr, payload_copy_rr
from evoos.export.embed import embed_document, pack_bin, unpack_bin, write_embed
from evoos.vm.instruction_set import Op, assemble
from evoos.vm.machine import lenient_config
from evoos.vm.sandbox import Sandbox


def test_opcodes_still_0_34():
    names = [op.name for op in Op]
    assert names[31] == "RDFAULT" and names[34] == "SVC"
    assert len(names) == 35


def test_routing_stages_shape():
    st = routing_stages()
    assert [s["name"] for s in st] == ["r1_pair", "r2_order", "r3_pairs", "r4_pressure"]
    assert st[0]["n_guests"] == 2 and len(st[0]["train"][0].guests) == 2
    assert len(st[1]["train"][0].guests[1].messages) == 3
    assert st[2]["n_guests"] == 6
    assert st[3]["n_guests"] == 8


def test_payload_copy_delivers_one_pair():
    cand = payload_copy_rr(16)
    sb = Sandbox(lenient_config(n_contexts=3, n_blocks=16, block_words=32, max_steps=4000, blk_latency=2))
    r = sb.evaluate(cand.instrs, route_workload("train", 1, name="t_r1"))
    assert not r.invalid
    assert r.delivered == 1
    assert r.isolation_ok
    assert all(r.msg_ok)


def test_payload_copy_preserves_order():
    cand = payload_copy_rr(16)
    sb = Sandbox(lenient_config(n_contexts=3, n_blocks=16, block_words=32, max_steps=4000, blk_latency=2))
    w = route_workload("train", 1, (7, 8, 9), name="t_r2")
    r = sb.evaluate(cand.instrs, w)
    assert r.delivered == 1
    assert r.msg_ok == [True, True, True]
    names, vec = case_vector([r], gated=True)
    assert "t_r2.m1.payload" in names
    assert vec[names.index("t_r2.m1.payload")] == 1.0


def test_fifo_still_one_word_route():
    cand = fifo_swap_rr(16)
    sb = Sandbox(lenient_config(n_contexts=3, n_blocks=16, block_words=32, max_steps=4000, blk_latency=2))
    r = sb.evaluate(cand.instrs, route_workload("train", 1, name="t_fifo"))
    assert r.delivered == 1 and r.isolation_ok


def test_shared_window_is_not_a_leak():
    """Producer store into a dual-mapped frame is granted sharing, not corruption."""
    cfg = lenient_config(n_contexts=3, n_blocks=4, block_words=32, max_steps=200)
    msrc = assemble(
        """
        MOVI r1, 1
        MOVI r2, 256
        CTXSET r1, r2, DATA_BASE
        MOVI r3, 288
        CTXSET r1, r3, DATA_BOUND
        ADDPROT r1, r2, r3
        MOVI r1, 2
        CTXSET r1, r2, DATA_BASE
        CTXSET r1, r3, DATA_BOUND
        ADDPROT r1, r2, r3
        MOVI r1, 1
        RUNCTXI r1, 16
        MOVI r1, 2
        RUNCTXI r1, 16
        HALT
        """
    )
    from evoos.vm.sandbox import GuestImage, Workload

    prod = GuestImage(
        name="p",
        code=assemble("MOVI r3, 99\nSTORE r1, r3, 0\nHALT"),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        role="producer",
        token=99,
        pair=2,
        messages=(99,),
    )
    cons = GuestImage(
        name="c",
        code=assemble("HALT"),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        expected_off={0: 99},
        role="consumer",
        token=99,
        pair=1,
        messages=(99,),
    )
    sb = Sandbox(cfg)
    r = sb.evaluate(msrc, Workload("share", (prod, cons), kind="route"))
    assert r.isolation_ok
    assert r.shared_window
    assert r.delivered == 1
    assert r.route_style == "shared-window"


def test_embed_roundtrip(tmp_path):
    instrs = assemble("MOVI r1, 1\nRUNCTXI r1, 8\nHALT")
    doc = embed_document(supervisor=instrs, origin="test", cases=routing_stages()[0]["train"])
    p = tmp_path / "elite.json"
    write_embed(p, doc, instrs)
    back = unpack_bin((tmp_path / "elite.bin").read_bytes())
    assert back == instrs
    assert doc["format"] == "evoos-embed-v1"
    assert pack_bin(instrs)[:4] == b"SOVE" or pack_bin(instrs)[0:4]  # endian check below
    import struct

    magic = struct.unpack_from("<I", pack_bin(instrs), 0)[0]
    assert magic == 0x45564F53
