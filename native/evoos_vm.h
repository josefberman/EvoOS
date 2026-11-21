#ifndef EVOOS_VM_H
#define EVOOS_VM_H

#include <stddef.h>
#include <stdint.h>

#define EVOOS_N_REGS 8
#define EVOOS_MAX_MEM 4096
#define EVOOS_MAX_CTX 8
#define EVOOS_MAX_CODE 256
#define EVOOS_MAX_PROT 8
#define EVOOS_MAX_RUNLOG 4096
#define EVOOS_MAX_ISOLATION 512

#define EVOOS_PROG_MAGIC 0x000E2015u
#define EVOOS_PROG_VERSION 1
#define EVOOS_IMAGE_MAGIC 0x30535645u
#define EVOOS_IMAGE_VERSION 1
#define EVOOS_IMAGE_HEADER 160

#define EVOOS_FLAG_STICKY_FAULT 1u
#define EVOOS_FLAG_SUP_HALT_MEM 2u
#define EVOOS_FLAG_RUNCTX_STATUS 4u

#define EVOOS_PERM_R 1
#define EVOOS_PERM_W 2
#define EVOOS_PERM_RW 3

enum {
    EVOOS_HALT = 0,
    EVOOS_NOP = 1,
    EVOOS_MOV = 2,
    EVOOS_MOVI = 3,
    EVOOS_ADD = 4,
    EVOOS_SUB = 5,
    EVOOS_MUL = 6,
    EVOOS_AND = 7,
    EVOOS_OR = 8,
    EVOOS_XOR = 9,
    EVOOS_SHL = 10,
    EVOOS_SHR = 11,
    EVOOS_NOT = 12,
    EVOOS_ADDI = 13,
    EVOOS_LOAD = 14,
    EVOOS_STORE = 15,
    EVOOS_CMP = 16,
    EVOOS_BEQ = 17,
    EVOOS_BNE = 18,
    EVOOS_BLT = 19,
    EVOOS_BGE = 20,
    EVOOS_JMP = 21,
    EVOOS_JAL = 22,
    EVOOS_JR = 23,
    EVOOS_RUNCTX = 24,
    EVOOS_RUNCTXI = 25,
    EVOOS_CTXGET = 26,
    EVOOS_CTXSET = 27,
    EVOOS_ADDPROT = 28,
    EVOOS_CLRPROT = 29,
    EVOOS_RDTIME = 30,
    EVOOS_RDFAULT = 31
};

enum {
    EVOOS_FLD_PC = 0,
    EVOOS_FLD_HALTED = 1,
    EVOOS_FLD_FAULT = 2,
    EVOOS_FLD_CYCLES = 3,
    EVOOS_FLD_DATA_BASE = 4,
    EVOOS_FLD_DATA_BOUND = 5,
    EVOOS_FLD_N_CONTEXTS = 6,
    EVOOS_FLD_STATUS = 7,
    EVOOS_FLD_LAST_RUN_STEPS = 8,
    EVOOS_FLD_LAST_RUN_FAULT = 9,
    EVOOS_FLD_LAST_RUN_CTX = 10
};

enum {
    EVOOS_FAULT_NONE = 0,
    EVOOS_FAULT_PROT = 1,
    EVOOS_FAULT_MEM = 2,
    EVOOS_FAULT_PC = 3,
    EVOOS_FAULT_CTX = 4,
    EVOOS_FAULT_PRIV = 5,
    EVOOS_FAULT_PROT_FULL = 6,
    EVOOS_FAULT_BAD_OP = 7,
    EVOOS_FAULT_BUDGET = 8
};

typedef struct {
    int32_t op, a, b, c, imm;
} EvoOSInstr;

typedef struct {
    EvoOSInstr code[EVOOS_MAX_CODE];
    int32_t n_code;
    int32_t pc;
    int32_t regs[EVOOS_N_REGS];
    int32_t halted;
    int32_t fault;
    int32_t cycles;
    int32_t halt_cycle; /* -1 if none */
    int32_t data_base;
    int32_t data_bound;
    int32_t prot_lo[EVOOS_MAX_PROT];
    int32_t prot_hi[EVOOS_MAX_PROT];
    int32_t prot_fl[EVOOS_MAX_PROT];
    int32_t n_prot;
} EvoOSContext;

typedef struct {
    int32_t mem_words;
    int32_t n_regs;
    int32_t n_contexts;
    int32_t max_steps;
    int32_t wall_steps;
    int32_t max_prot_regions;
    int32_t sticky_guest_fault;
    int32_t supervisor_halt_on_mem;
    int32_t runctx_posts_status;
    int32_t io_base;
    int32_t io_bound;

    int32_t mem[EVOOS_MAX_MEM];
    uint8_t last_writer[EVOOS_MAX_MEM];
    EvoOSContext contexts[EVOOS_MAX_CTX];
    int32_t cycles;
    int32_t halted;
    int32_t supervisor_fault;
    int32_t switch_count;
    int32_t last_run_steps;
    int32_t last_run_fault;
    int32_t last_run_ctx;

    int32_t legal_lo[EVOOS_MAX_CTX];
    int32_t legal_hi[EVOOS_MAX_CTX];
    int32_t legal_set[EVOOS_MAX_CTX];

    int32_t iso_cid[EVOOS_MAX_ISOLATION];
    int32_t iso_addr[EVOOS_MAX_ISOLATION];
    int32_t isolation_n;

    int32_t runlog_cyc[EVOOS_MAX_RUNLOG];
    int32_t runlog_cid[EVOOS_MAX_RUNLOG];
    int32_t runlog_ran[EVOOS_MAX_RUNLOG];
    int32_t runctx_n;

    char origin[32];
    char workload[32];
    char guest_name[EVOOS_MAX_CTX][32];
    int32_t n_guests;
    int32_t exp_cid[256];
    int32_t exp_addr[256];
    int32_t exp_val[256];
    int32_t n_expected;
} EvoOSMachine;

typedef void (*evoos_emit_fn)(const char *line);
typedef void (*evoos_store_hook_fn)(int32_t addr, int32_t val);
typedef void (*evoos_runctx_hook_fn)(const EvoOSMachine *m, int32_t cid, int32_t ran);

extern evoos_store_hook_fn evoos_store_hook;
extern evoos_runctx_hook_fn evoos_runctx_hook;

int evoos_load_image(EvoOSMachine *m, const uint8_t *data, size_t len);
void evoos_run(EvoOSMachine *m, int32_t max_steps);
void evoos_dump_trace(const EvoOSMachine *m, evoos_emit_fn emit);
uint32_t evoos_mem_fingerprint(const EvoOSMachine *m);

#endif
