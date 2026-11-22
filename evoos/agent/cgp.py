"""1-row Cartesian GP genotype over the frozen ISA.

Each node is (op, conn1, conn2, imm). Connections index earlier nodes and
are mapped onto registers. Decode emits ordinary 5-tuples; the VM ISA is
unchanged. This is a *representation* of search, not an OS primitive.
"""

from __future__ import annotations

import random

from evoos.agent.mutations import _BIASED_OPS, _OPS
from evoos.agent.program import Candidate
from evoos.vm.instruction_set import N_REGS, Instr, Op


def random_genome(rng: random.Random, n_nodes: int, const_pool: tuple[int, ...] = ()) -> tuple:
    nodes = []
    for i in range(max(1, n_nodes)):
        op = rng.choice(_BIASED_OPS)
        c1 = rng.randrange(i) if i else 0
        c2 = rng.randrange(i) if i else 0
        if const_pool and rng.random() < 0.5:
            imm = int(const_pool[rng.randrange(len(const_pool))])
        else:
            imm = rng.choice([0, 1, 2, 3, 4, 8, 16, 32, 64, 256, 384, 512])
        nodes.append((int(op), int(c1), int(c2), int(imm)))
    return tuple(nodes)


def decode(genome: tuple) -> tuple[Instr, ...]:
    instrs: list[Instr] = []
    for i, node in enumerate(genome):
        op, c1, c2, imm = node[:4]
        a = i % N_REGS
        b = (int(c1) % N_REGS) if i == 0 else (int(c1) % max(i, 1)) % N_REGS
        c = (int(c2) % N_REGS) if i == 0 else (int(c2) % max(i, 1)) % N_REGS
        instrs.append(Instr(int(op), a, b, c, int(imm)))
    instrs.append(Instr(int(Op.HALT), 0, 0, 0, 0))
    return tuple(instrs)


def mutate_genome(
    genome: tuple,
    rng: random.Random,
    *,
    const_pool: tuple[int, ...] = (),
) -> tuple:
    nodes = [list(n) for n in genome]
    if not nodes:
        return random_genome(rng, 8, const_pool)
    i = rng.randrange(len(nodes))
    which = rng.choice(("op", "c1", "c2", "imm"))
    if which == "op":
        nodes[i][0] = int(rng.choice(_OPS))
    elif which == "c1":
        nodes[i][1] = rng.randrange(i) if i else 0
    elif which == "c2":
        nodes[i][2] = rng.randrange(i) if i else 0
    else:
        if const_pool and rng.random() < 0.6:
            nodes[i][3] = int(const_pool[rng.randrange(len(const_pool))])
        else:
            nodes[i][3] += rng.choice([-16, -4, -1, 1, 4, 16])
    return tuple(tuple(n) for n in nodes)


def genome_from_instrs(instrs) -> tuple:
    """Rebuild a 1-row genome from decoded 5-tuples (drops a trailing HALT)."""
    nodes = []
    seq = list(instrs)
    if seq and seq[-1].op == int(Op.HALT):
        seq = seq[:-1]
    for ins in seq:
        nodes.append((int(ins.op), int(ins.b), int(ins.c), int(ins.imm)))
    return tuple(nodes) if nodes else ((int(Op.HALT), 0, 0, 0),)


def random_candidate(rng: random.Random, n_nodes: int, const_pool: tuple[int, ...] = ()) -> tuple[Candidate, tuple]:
    g = random_genome(rng, n_nodes, const_pool)
    return Candidate(instrs=decode(g), origin="cgp"), g
