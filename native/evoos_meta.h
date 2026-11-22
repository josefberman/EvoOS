#ifndef EVOOS_META_H
#define EVOOS_META_H

/*
 * On-device evolution engine. Firmware / meta layer, not OS policy.
 * Mutates supervisor bytecode, scores candidates in an isolated C-interpreter
 * shadow, and hot-swaps the live interpreter. Guest contexts persist.
 */

#include <stdint.h>

#define META_POP 8
#define META_MAX_CODE 128
#define META_TRACE 48
#define META_MARGIN_MILLI 30
#define META_LBA0 64
#define META_MAP_X 4
#define META_MAP_Y 4
#define META_N_CASES 4
#define META_HELD 6
#define META_LBA_MAP 80

typedef struct {
    int32_t generation;
    int32_t incumbent_id;
    int32_t parent_id;
    int32_t live_fit;
    int32_t shadow_fit;
    int32_t evals;
    int32_t swaps;
    int32_t rollbacks;
    int32_t evo_on;
    int32_t n_code;
    int32_t resumed;
    int32_t lineage_n;
    int32_t lineage[8];
} EvoOSMetaStats;

void evoos_firmware_log(const char *s);
void evoos_firmware_log_i32(int32_t v);

void evoos_meta_init(void);
void evoos_meta_toggle(void);
void evoos_meta_tick(void);
int evoos_meta_live_burst(int n_ops);
void evoos_meta_record_spawn(int slot, const char *kind);
void evoos_meta_record_svc(int32_t code, int32_t ctx);
void evoos_meta_on_live_metrics(int32_t isolation_n);
void evoos_meta_show_lineage(void);
void evoos_meta_inject_degrader(void);
void evoos_meta_on_supervisor_halt(void);
EvoOSMetaStats evoos_meta_stats(void);
int evoos_meta_using_interp(void);

#endif
