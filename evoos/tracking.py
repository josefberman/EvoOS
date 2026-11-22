"""SQLite experiment logs. TensorBoard is optional."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed INTEGER,
    mode TEXT,
    started_at REAL,
    config_json TEXT
);
CREATE TABLE IF NOT EXISTS generations (
    run_id INTEGER,
    gen INTEGER,
    best_reward REAL,
    mean_reward REAL,
    mean_valid REAL,
    n_invalid INTEGER,
    n_eval INTEGER,
    archive_size INTEGER,
    best_f REAL,
    best_completed INTEGER
);
CREATE TABLE IF NOT EXISTS evaluations (
    run_id INTEGER,
    gen INTEGER,
    candidate_id INTEGER,
    origin TEXT,
    reward REAL,
    f REAL, s REAL, e REAL, g REAL, n REAL,
    hard_fail INTEGER,
    invalid INTEGER,
    completed INTEGER,
    switches INTEGER,
    program_json TEXT
);
CREATE TABLE IF NOT EXISTS archive (
    run_id INTEGER,
    cell_x INTEGER,
    cell_y INTEGER,
    reward REAL,
    generation INTEGER,
    program_json TEXT
);
"""


class ExperimentLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.run_id = 0
        self._tb = None

    def start_run(self, seed: int, mode: str, config: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs(seed, mode, started_at, config_json) VALUES (?,?,?,?)",
            (seed, mode, time.time(), json.dumps(config)),
        )
        self.conn.commit()
        self.run_id = int(cur.lastrowid)
        self._maybe_tb()
        return self.run_id

    def _maybe_tb(self) -> None:
        try:
            from torch.utils.tensorboard import SummaryWriter

            logdir = self.path.parent / "tb"
            self._tb = SummaryWriter(str(logdir))
        except Exception:
            self._tb = None

    def log_generation(self, row: dict) -> None:
        self.conn.execute(
            """INSERT INTO generations(
                run_id, gen, best_reward, mean_reward, mean_valid, n_invalid,
                n_eval, archive_size, best_f, best_completed
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                self.run_id,
                row["generation"],
                row["best_reward"],
                row["mean_reward"],
                row.get("mean_valid", 0),
                row["n_invalid"],
                row["n_eval"],
                row["archive_size"],
                row.get("best_f", 0),
                row.get("best_completed", 0),
            ),
        )
        self.conn.commit()
        if self._tb is not None:
            g = row["generation"]
            self._tb.add_scalar("reward/best", row["best_reward"], g)
            self._tb.add_scalar("reward/mean", row["mean_reward"], g)
            self._tb.add_scalar("archive/size", row["archive_size"], g)

    def log_eval(
        self,
        gen: int,
        candidate_id: int,
        origin: str,
        reward,
        switches: int,
        program_json: str,
        invalid: bool,
    ) -> None:
        self.conn.execute(
            """INSERT INTO evaluations(
                run_id, gen, candidate_id, origin, reward, f, s, e, g, n,
                hard_fail, invalid, completed, switches, program_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self.run_id,
                gen,
                candidate_id,
                origin,
                reward.total,
                reward.f,
                reward.s,
                reward.e,
                reward.g,
                reward.n,
                int(reward.hard_fail),
                int(invalid),
                reward.completed,
                switches,
                program_json,
            ),
        )
        self.conn.commit()

    def log_archive(self, cells: list[dict], programs: dict[tuple, str]) -> None:
        self.conn.execute("DELETE FROM archive WHERE run_id=?", (self.run_id,))
        for row in cells:
            x, y = row["cell"]
            self.conn.execute(
                """INSERT INTO archive(run_id, cell_x, cell_y, reward, generation, program_json)
                   VALUES (?,?,?,?,?,?)""",
                (self.run_id, x, y, row["reward"], row["generation"], programs.get((x, y), "")),
            )
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
        if self._tb is not None:
            self._tb.close()
