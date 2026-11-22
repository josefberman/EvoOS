"""Fixed-size block device. No naming, no layout, no journal.

The supervisor chooses which blocks hold what. Hardware only copies words
with latency. Not a paging primitive.
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
    last_status: int = int(BlkStatus.OK)
    n_read: int = 0
    n_write: int = 0

    def __post_init__(self) -> None:
        if not self.blocks:
            self.blocks = [[0] * self.block_words for _ in range(self.n_blocks)]

    def clone(self) -> "BlockDevice":
        return BlockDevice(
            n_blocks=self.n_blocks,
            block_words=self.block_words,
            latency=self.latency,
            blocks=[b[:] for b in self.blocks],
            last_status=self.last_status,
            n_read=self.n_read,
            n_write=self.n_write,
        )

    def read(self, blk: int) -> tuple[int, list[int]]:
        blk = int(blk)
        self.n_read += 1
        if blk < 0 or blk >= self.n_blocks:
            self.last_status = int(BlkStatus.OOB)
            return self.last_status, [0] * self.block_words
        self.last_status = int(BlkStatus.OK)
        return self.last_status, self.blocks[blk][:]

    def write(self, blk: int, words: list[int]) -> int:
        blk = int(blk)
        payload = [_w32(w) for w in words[: self.block_words]]
        while len(payload) < self.block_words:
            payload.append(0)
        self.n_write += 1
        if blk < 0 or blk >= self.n_blocks:
            self.last_status = int(BlkStatus.OOB)
            return self.last_status
        self.blocks[blk] = payload
        self.last_status = int(BlkStatus.OK)
        return self.last_status
