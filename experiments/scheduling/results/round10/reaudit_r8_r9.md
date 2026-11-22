# Round 8/9 re-audit under on-device 8-ctx rule

n_contexts=**8**. Pair window `(256, 384)`. Live HALT guests fill ctx ids above n_guests. Any grant of a pair window or subrange to a non-participant is a leak.

Admission pass: **0/14**.

| name | admission | n_leak | attacker_fail | failing cases |
| --- | --- | --- | --- | --- |
| payload_copy_rr | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| fifo_swap_rr | False | 3 | 0 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| route_s1 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| route_s13 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| route_s2024 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| route_s42 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| route_s7 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| route_s99 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| gen_s1 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| gen_s13 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| gen_s2024 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| gen_s42 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| gen_s7 | False | 3 | 3 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |
| gen_s99 | False | 3 | 1 | audit_device_pair_atk, audit_device_pair_load, audit_device_pair_store |

