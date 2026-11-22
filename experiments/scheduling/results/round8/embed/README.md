Boot-track elites for on-device evolution.

- Format: `evoos-embed-v1` (spec: [`evoos/export/FORMAT.md`](../../../../../evoos/export/FORMAT.md)).
- JSON 5-tuples are canonical. `.bin` is the same instructions as little-endian `i32×5`.
- `cases[]` are the routing curriculum workloads plus the original 4-guest mix.
- Load with `from evoos.export import read_embed, unpack_bin`.
