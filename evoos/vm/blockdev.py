"""Fixed-size block device. No naming, no layout, no journal.

The supervisor chooses which blocks hold what. Hardware (and this model)
only copies bytes with latency and optional corruption / dropped writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evoos.vm.instruction_set import BlkStatus


def _w32(x: int) -> int:
    x &= 0xFFFFFFFF
    if x & 0x80000000:
        return x - 0x100000000
    return x

DEFAULT_N_BLOCKS = 32
DEFAULT_BLK_WORDS = 16
DEFAULT_LATENCY = 4


@dataclass
class BlockDevice:
    n_blocks: int = DEFAULT_N_BLOCKS
    block_words: int = DEFAULT_BLK_WORDS
    latency: int = DEFAULT_LATENCY
    blocks: list[list[int]] = field(default_factory=list)
    bad: set[int] = field(default_factory=set)
    last_status: int = int(BlkStatus.OK)
    n_read: int = 0
    n_write: int = 0
    inflight: tuple[int, tuple[int, ...]] | None = None
    drop_next_write: bool = False

    def __post_init__(self) -> None:
        if not self.blocks:
            self.blocks = [[0] * self.block_words for _ in range(self.n_blocks)]

    def clone(self) -> "BlockDevice":
        d = BlockDevice(
            n_blocks=self.n_blocks,
            block_words=self.block_words,
            latency=self.latency,
            blocks=[b[:] for b in self.blocks],
            bad=set(self.bad),
            last_status=self.last_status,
            n_read=self.n_read,
            n_write=self.n_write,
            inflight=self.inflight,
            drop_next_write=self.drop_next_write,
        )
        return d

    def _oob(self, blk: int) -> bool:
        return blk < 0 or blk >= self.n_blocks

    def read(self, blk: int) -> tuple[int, list[int]]:
        blk = int(blk)
        self.n_read += 1
        if self._oob(blk):
            self.last_status = int(BlkStatus.OOB)
            return self.last_status, [0] * self.block_words
        if blk in self.bad:
            self.last_status = int(BlkStatus.BAD)
            return self.last_status, [0] * self.block_words
        self.last_status = int(BlkStatus.OK)
        return self.last_status, self.blocks[blk][:]

    def write(self, blk: int, words: list[int]) -> int:
        blk = int(blk)
        payload = [_w32(w) for w in words[: self.block_words]]
        while len(payload) < self.block_words:
            payload.append(0)
        self.n_write += 1
        if self._oob(blk):
            self.last_status = int(BlkStatus.OOB)
            return self.last_status
        if blk in self.bad:
            self.last_status = int(BlkStatus.BAD)
            return self.last_status
        if self.drop_next_write:
            self.inflight = (blk, tuple(payload))
            self.drop_next_write = False
            self.last_status = int(BlkStatus.OK)
            return self.last_status
        self.blocks[blk] = payload
        self.last_status = int(BlkStatus.OK)
        return self.last_status

    def power_cut(self, drop_inflight: bool = True) -> None:
        """Keep committed blocks. Optionally discard a write that had not landed."""
        if drop_inflight:
            self.inflight = None
        elif self.inflight is not None:
            blk, payload = self.inflight
            if 0 <= blk < self.n_blocks and blk not in self.bad:
                self.blocks[blk] = list(payload)
            self.inflight = None
        self.drop_next_write = False

    def as_rows(self) -> list[list[int]]:
        return [b[:] for b in self.blocks]
