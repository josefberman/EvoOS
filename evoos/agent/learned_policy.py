"""GRU edit policy over bytecode tokens + milestone telemetry.

ISA is not changed. The network proposes *edits* (kind, position, operands)
to existing supervisor programs. Trained with REINFORCE + a value baseline
(one-step PPO clip on the collected batch).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from evoos.agent.mutations import BARE_OPS, apply_edit
from evoos.agent.program import Candidate
from evoos.vm.instruction_set import N_REGS, Instr, Op
from evoos.vm.sandbox import EvalResult


EDIT_KINDS = BARE_OPS
N_EDIT = len(EDIT_KINDS)
MAX_LEN = 256
ENC_LEN = 48
IMM_TABLE = (
    -32, -16, -8, -4, -1, 0, 1, 2, 3, 4, 5, 8, 12, 16, 24, 32, 64, 128, 256, 384, 512, 640
)
N_IMM = len(IMM_TABLE)
N_OP = 35
TELE_DIM = 12


def telemetry_vec(result: EvalResult | None, reward: float = 0.0) -> list[float]:
    if result is None:
        return [0.0] * TELE_DIM
    n = max(result.n_guests, 1)
    cover = sum(1 for c in (result.prot_cover or []) if c) / n
    return [
        cover,
        result.entered_protected / n,
        result.progressed_protected / n,
        result.completed_protected / n,
        result.n_guests / 4.0,
        max(-1.0, min(1.0, reward)),
        1.0 if result.isolation_ok else 0.0,
        min(result.switch_count / 50.0, 1.0),
        result.entered / n,
        result.completed / n,
        1.0 if cover > 0 else 0.0,
        1.0 if result.entered_protected else 0.0,
    ]


def _instr_row(ins: Instr) -> list[float]:
    return [
        ins.op / max(N_OP - 1, 1),
        ins.a / max(N_REGS - 1, 1),
        ins.b / max(N_REGS - 1, 1),
        ins.c / max(N_REGS - 1, 1),
        math.tanh(ins.imm / 64.0),
    ]


@dataclass
class SampledEdit:
    kind: str
    pos: int
    instr: Instr
    log_prob: object  # torch.Tensor | None (recomputed in learn_batch)
    value: object
    kind_idx: int
    pos_idx: int
    op_idx: int
    a_idx: int
    b_idx: int
    c_idx: int
    imm_idx: int
    rows: list = field(default_factory=list)
    tele: list = field(default_factory=list)
    n_instr: int = 0


class LearnedEditPolicy:
    """Small GRU policy. Falls back to UCB1 if torch is missing at runtime."""

    def __init__(
        self,
        operators: tuple[str, ...] = EDIT_KINDS,
        hidden: int = 48,
        lr: float = 3e-4,
        epsilon: float = 0.12,
        new_cell_bonus: float = 0.08,
        ppo_clip: float = 0.2,
        ppo_epochs: int = 1,
    ) -> None:
        self.operators = operators
        self.epsilon = epsilon
        self.new_cell_bonus = new_cell_bonus
        self.ppo_clip = ppo_clip
        self.ppo_epochs = ppo_epochs
        self.kind_counts = {k: 0 for k in operators}
        self.kind_adv = {k: 0.0 for k in operators}
        self.n_updates = 0
        self._torch = False
        self.net = None
        self.opt = None
        from evoos.agent.policy import UCB1Bandit

        self._ucb = UCB1Bandit(operators=operators)
        try:
            import torch
            import torch.nn as nn

            class EditNet(nn.Module):
                def __init__(self) -> None:
                    super().__init__()
                    self.gru = nn.GRU(5, hidden, num_layers=1, batch_first=True)
                    self.tele = nn.Linear(TELE_DIM, hidden)
                    self.fuse = nn.Linear(hidden * 2, hidden)
                    self.kind = nn.Linear(hidden, N_EDIT)
                    self.pos = nn.Linear(hidden, MAX_LEN)
                    self.op = nn.Linear(hidden, N_OP)
                    self.ra = nn.Linear(hidden, N_REGS)
                    self.rb = nn.Linear(hidden, N_REGS)
                    self.rc = nn.Linear(hidden, N_REGS)
                    self.imm = nn.Linear(hidden, N_IMM)
                    self.v = nn.Linear(hidden, 1)

                def encode(self, rows, tele, n_instr: int):
                    rows = rows[-ENC_LEN:] or [[0.0] * 5]
                    x = torch.tensor(rows, dtype=torch.float32).unsqueeze(0)
                    if x.size(1) == 0:
                        h = torch.zeros(1, hidden)
                    else:
                        _, h = self.gru(x)
                        h = h[-1]
                    t = self.tele(torch.tensor(tele, dtype=torch.float32).unsqueeze(0))
                    z = torch.tanh(self.fuse(torch.cat([h, t], dim=-1)))
                    return z, n_instr

                def encode_many(self, rows_list, tele_list):
                    B = len(rows_list)
                    seqs = [r[-ENC_LEN:] or [[0.0] * 5] for r in rows_list]
                    T = max(len(s) for s in seqs)
                    x = torch.zeros(B, T, 5)
                    for i, s in enumerate(seqs):
                        x[i, : len(s)] = torch.tensor(s, dtype=torch.float32)
                    _, h = self.gru(x)
                    h = h[-1]
                    t = self.tele(torch.tensor(tele_list, dtype=torch.float32))
                    return torch.tanh(self.fuse(torch.cat([h, t], dim=-1)))

                def heads(self, z, n_instr):
                    kind_l = self.kind(z)
                    pos_l = self.pos(z)
                    mask = torch.full_like(pos_l, -1e9)
                    if pos_l.dim() == 1:
                        pos_l = pos_l.unsqueeze(0)
                        squeeze = True
                    else:
                        squeeze = False
                    if isinstance(n_instr, int):
                        ns = [n_instr]
                    else:
                        ns = list(n_instr)
                    for i, n in enumerate(ns):
                        cap = min(MAX_LEN, int(n) + 1)
                        mask[i, : max(cap, 1)] = 0.0
                    pos_l = pos_l + mask
                    outs = (kind_l, pos_l, self.op(z), self.ra(z), self.rb(z), self.rc(z), self.imm(z), self.v(z))
                    return outs

            self.net = EditNet()
            self.opt = torch.optim.Adam(self.net.parameters(), lr=lr)
            self._torch = True
            self._torch_mod = torch
            self._nn = nn
            try:
                torch.set_num_threads(1)
            except Exception:
                pass
        except Exception:
            self._torch = False

    def snapshot(self) -> dict:
        top = sorted(self.kind_counts.items(), key=lambda kv: -kv[1])[:8]
        preferred = sorted(self.kind_adv.items(), key=lambda kv: -kv[1])[:8]
        return {
            "kind": "reinforce_gru" if self._torch else "ucb1_fallback",
            "n_updates": self.n_updates,
            "kind_counts": dict(self.kind_counts),
            "kind_adv": dict(self.kind_adv),
            "top_edits": top,
            "preferred_edits": preferred,
            "torch": self._torch,
            "ucb": self._ucb.snapshot(),
        }

    def select_operator(self, rng: random.Random) -> str:
        return self._ucb.select_operator(rng)

    def update(self, operator: str, reward: float) -> None:
        self._ucb.update(operator, reward)
        self.kind_counts[operator] = self.kind_counts.get(operator, 0) + 1

    def act(
        self,
        parent: Candidate,
        tele: list[float],
        rng: random.Random,
        *,
        max_len: int = 256,
        other: Candidate | None = None,
        hall: list[Candidate] | None = None,
    ) -> tuple[Candidate, SampledEdit | None]:
        if (not self._torch) or rng.random() < self.epsilon:
            from evoos.agent.mutations import mutate

            kind = self.select_operator(rng)
            child = mutate(parent, kind, rng, other=other, hall=hall, max_len=max_len, operators=self.operators)
            return child, None
        torch = self._torch_mod
        rows = [_instr_row(ins) for ins in parent.instrs[:MAX_LEN]] or [[0.0] * 5]
        n = len(parent.instrs)
        with torch.no_grad():
            z, _ = self.net.encode(rows, tele, n)
            kind_l, pos_l, op_l, a_l, b_l, c_l, imm_l, v = self.net.heads(z, n)
            ki = torch.distributions.Categorical(logits=kind_l).sample()
            pi = torch.distributions.Categorical(logits=pos_l).sample()
            oi = torch.distributions.Categorical(logits=op_l).sample()
            ai = torch.distributions.Categorical(logits=a_l).sample()
            bi = torch.distributions.Categorical(logits=b_l).sample()
            ci = torch.distributions.Categorical(logits=c_l).sample()
            ii = torch.distributions.Categorical(logits=imm_l).sample()
        kind = EDIT_KINDS[int(ki.item())]
        pos = int(pi.item())
        instr = Instr(int(oi.item()), int(ai.item()), int(bi.item()), int(ci.item()), int(IMM_TABLE[int(ii.item())]))
        if kind == "control_flow":
            cf = [int(Op.JMP), int(Op.BEQ), int(Op.BNE), int(Op.BLT), int(Op.BGE), int(Op.JAL)]
            instr = Instr(cf[int(oi.item()) % len(cf)], instr.a, instr.b, instr.c, instr.imm)
        child = apply_edit(parent, kind, pos, new_instr=instr, other=other, hall=hall, rng=rng, max_len=max_len, operators=self.operators)
        sampled = SampledEdit(
            kind=kind,
            pos=pos,
            instr=instr,
            log_prob=None,
            value=None,
            kind_idx=int(ki.item()),
            pos_idx=pos,
            op_idx=int(oi.item()),
            a_idx=int(ai.item()),
            b_idx=int(bi.item()),
            c_idx=int(ci.item()),
            imm_idx=int(ii.item()),
            rows=rows,
            tele=list(tele),
            n_instr=n,
        )
        return child, sampled

    def act_batch(
        self,
        parents: list[Candidate],
        teles: list[list[float]],
        rng: random.Random,
        *,
        max_len: int = 256,
        others: list[Candidate | None] | None = None,
        hall: list[Candidate] | None = None,
    ) -> list[tuple[Candidate, SampledEdit | None]]:
        """Vectorized no-grad sample for a generation; epsilon items use UCB mutate."""
        others = others or [None] * len(parents)
        if not self._torch:
            return [
                self.act(p, t, rng, max_len=max_len, other=o, hall=hall)
                for p, t, o in zip(parents, teles, others)
            ]
        torch = self._torch_mod
        flags = [rng.random() < self.epsilon for _ in parents]
        out: list[tuple[Candidate, SampledEdit | None] | None] = [None] * len(parents)
        net_idx = [i for i, e in enumerate(flags) if not e]
        from evoos.agent.mutations import mutate as _mutate

        for i, e in enumerate(flags):
            if e:
                kind = self.select_operator(rng)
                child = _mutate(
                    parents[i],
                    kind,
                    rng,
                    other=others[i],
                    hall=hall,
                    max_len=max_len,
                    operators=self.operators,
                )
                out[i] = (child, None)
        if net_idx:
            rows_list = [
                [_instr_row(ins) for ins in parents[i].instrs[:MAX_LEN]] or [[0.0] * 5] for i in net_idx
            ]
            tele_list = [teles[i] for i in net_idx]
            ns = [len(parents[i].instrs) for i in net_idx]
            with torch.no_grad():
                z = self.net.encode_many(rows_list, tele_list)
                kind_l, pos_l, op_l, a_l, b_l, c_l, imm_l, _v = self.net.heads(z, ns)
                ki = torch.distributions.Categorical(logits=kind_l).sample()
                pi = torch.distributions.Categorical(logits=pos_l).sample()
                oi = torch.distributions.Categorical(logits=op_l).sample()
                ai = torch.distributions.Categorical(logits=a_l).sample()
                bi = torch.distributions.Categorical(logits=b_l).sample()
                ci = torch.distributions.Categorical(logits=c_l).sample()
                ii = torch.distributions.Categorical(logits=imm_l).sample()
            cf = [int(Op.JMP), int(Op.BEQ), int(Op.BNE), int(Op.BLT), int(Op.BGE), int(Op.JAL)]
            for j, i in enumerate(net_idx):
                kind = EDIT_KINDS[int(ki[j].item())]
                pos = int(pi[j].item())
                instr = Instr(
                    int(oi[j].item()),
                    int(ai[j].item()),
                    int(bi[j].item()),
                    int(ci[j].item()),
                    int(IMM_TABLE[int(ii[j].item())]),
                )
                if kind == "control_flow":
                    instr = Instr(cf[int(oi[j].item()) % len(cf)], instr.a, instr.b, instr.c, instr.imm)
                child = apply_edit(
                    parents[i],
                    kind,
                    pos,
                    new_instr=instr,
                    other=others[i],
                    hall=hall,
                    rng=rng,
                    max_len=max_len,
                    operators=self.operators,
                )
                sampled = SampledEdit(
                    kind=kind,
                    pos=pos,
                    instr=instr,
                    log_prob=None,
                    value=None,
                    kind_idx=int(ki[j].item()),
                    pos_idx=pos,
                    op_idx=int(oi[j].item()),
                    a_idx=int(ai[j].item()),
                    b_idx=int(bi[j].item()),
                    c_idx=int(ci[j].item()),
                    imm_idx=int(ii[j].item()),
                    rows=rows_list[j],
                    tele=list(tele_list[j]),
                    n_instr=ns[j],
                )
                out[i] = (child, sampled)
        return [x if x is not None else self.act(parents[k], teles[k], rng, max_len=max_len) for k, x in enumerate(out)]

    def _logp_value(self, s: SampledEdit):
        logps, values = self._logp_value_batch([s])
        return logps[0], values[0]

    def _logp_value_batch(self, sampled: list[SampledEdit]):
        torch = self._torch_mod
        z = self.net.encode_many([s.rows for s in sampled], [s.tele for s in sampled])
        ns = [s.n_instr for s in sampled]
        kind_l, pos_l, op_l, a_l, b_l, c_l, imm_l, v = self.net.heads(z, ns)

        def col(logits, idxs):
            d = torch.distributions.Categorical(logits=logits)
            return d.log_prob(torch.tensor(idxs, dtype=torch.long))

        logp = (
            col(kind_l, [s.kind_idx for s in sampled])
            + col(pos_l, [s.pos_idx for s in sampled])
            + col(op_l, [s.op_idx for s in sampled])
            + col(a_l, [s.a_idx for s in sampled])
            + col(b_l, [s.b_idx for s in sampled])
            + col(c_l, [s.c_idx for s in sampled])
            + col(imm_l, [s.imm_idx for s in sampled])
        )
        return logp, v.reshape(-1)

    def learn_batch(self, samples: list[tuple[SampledEdit, float]]) -> float:
        """REINFORCE / PPO on (child_R - parent_R + new-cell bonus). Re-forwards each epoch."""
        if not self._torch or not samples:
            return 0.0
        torch = self._torch_mod
        usable = [(s, a) for s, a in samples if s.rows]
        if not usable:
            return 0.0
        advs = torch.tensor([a for _, a in usable], dtype=torch.float32)
        advs = advs - advs.mean()
        std = advs.std()
        if float(std) > 1e-6:
            advs = advs / (std + 1e-6)
        with torch.no_grad():
            old_logps = self._logp_value_batch([s for s, _ in usable])[0].detach()
        loss_v = 0.0
        for _ in range(max(1, self.ppo_epochs)):
            logps, values = self._logp_value_batch([s for s, _ in usable])
            advantages = advs - values.detach()
            ratio = torch.exp(logps - old_logps)
            unclipped = ratio * advantages
            clipped = torch.clamp(ratio, 1.0 - self.ppo_clip, 1.0 + self.ppo_clip) * advantages
            pol = -torch.min(unclipped, clipped).mean()
            vf = 0.5 * (values - advs).pow(2).mean()
            loss = pol + 0.5 * vf
            self.opt.zero_grad()
            loss.backward()
            self._nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
            self.opt.step()
            loss_v = float(loss.detach())
        self.n_updates += 1
        for s, a in usable:
            prev = self.kind_adv.get(s.kind, 0.0)
            self.kind_adv[s.kind] = 0.9 * prev + 0.1 * float(a)
        return loss_v

    def state_dict(self):
        if not self._torch:
            return {}
        return self.net.state_dict()

    def load_state_dict(self, d) -> None:
        if self._torch and d:
            self.net.load_state_dict(d)

    def save(self, path) -> None:
        if not self._torch:
            return
        self._torch_mod.save({"net": self.net.state_dict(), "counts": self.kind_counts, "n": self.n_updates}, path)
