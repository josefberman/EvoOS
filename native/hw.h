#ifndef EVOOS_HW_H
#define EVOOS_HW_H

#include <stdint.h>

#define HW_N_CTX 5
#define HW_N_GUESTS 4
#define HW_MAX_PROT 4
#define HW_MEM_WORDS 2048

#define HW_SEL_KCODE 0x08
#define HW_SEL_KDATA 0x10
#define HW_SEL_UCODE 0x1B
#define HW_SEL_UDATA 0x23
#define HW_SEL_TSS 0x28

#define HW_CODE_VA 0x0000000000400000ULL
#define HW_STACK_VA 0x0000000000501000ULL
#define HW_STACK_PAGE 0x0000000000500000ULL
#define HW_DATA_VA 0x0000000000600000ULL

#define HW_SLOT_BASE(s) (256 + (s) * 128)
#define HW_SLOT_BOUND(s) (HW_SLOT_BASE(s) + 128)
#define HW_DATA_VA_SLOT(s) (HW_DATA_VA + (uint64_t)(s) * 0x1000ULL)

#define HW_VEC_PF 14
#define HW_VEC_GP 13
#define HW_VEC_TIMER 32
#define HW_VEC_HALT 0x80

#define HW_FAULT_NONE 0
#define HW_FAULT_PROT 1
#define HW_FAULT_MEM 2
#define HW_FAULT_PC 3
#define HW_FAULT_CTX 4
#define HW_FAULT_PRIV 5
#define HW_FAULT_PROT_FULL 6
#define HW_FAULT_BAD_OP 7
#define HW_FAULT_BUDGET 8

#define HW_FLD_PC 0
#define HW_FLD_HALTED 1
#define HW_FLD_FAULT 2
#define HW_FLD_CYCLES 3
#define HW_FLD_DATA_BASE 4
#define HW_FLD_DATA_BOUND 5
#define HW_FLD_N_CONTEXTS 6
#define HW_FLD_STATUS 7
#define HW_FLD_LAST_RUN_STEPS 8
#define HW_FLD_LAST_RUN_FAULT 9
#define HW_FLD_LAST_RUN_CTX 10

typedef struct {
    uint64_t rip, rsp, rflags, rdi, rsi, rax, rbx, rcx, rdx, rbp;
    uint64_t r8, r9, r10, r11, r12, r13, r14, r15;
    uint64_t cr3;
    int32_t halted;
    int32_t fault;
    int32_t cycles;
    int32_t data_base;
    int32_t data_bound;
    int32_t prot_lo[HW_MAX_PROT];
    int32_t prot_hi[HW_MAX_PROT];
    int32_t n_prot;
    int32_t last_vector;
    uint64_t last_cr2;
    int32_t preempts;
    int32_t syscalls;
    char name[32];
    uint8_t *code_page;
    uint8_t *stack_page;
    uint8_t *data_page;
} HwContext;

typedef struct {
    HwContext ctx[HW_N_CTX];
    int32_t n_contexts;
    int32_t switch_count;
    int32_t last_run_steps;
    int32_t last_run_fault;
    int32_t last_run_ctx;
    int32_t cycles;
    int32_t io_base;
    int32_t extra[HW_MEM_WORDS];
    uint8_t slot_page[HW_N_GUESTS][4096];
    int32_t isolation_n;
} HwMachine;

extern HwMachine hw;
extern const char supervisor_origin[];
extern const char supervisor_note[];

void evoos_hw_init(uint64_t hhdm, uint64_t kphys, uint64_t kvirt);
int32_t evoos_hw_runctx(int32_t cid, int32_t n_steps);
int32_t evoos_hw_ctxget(int32_t cid, int32_t field);
void evoos_hw_ctxset(int32_t cid, int32_t field, int32_t val);
int evoos_hw_addprot(int32_t cid, int32_t lo, int32_t hi);
void evoos_hw_clrprot(int32_t cid);
int32_t evoos_hw_load(int32_t addr);
void evoos_hw_store(int32_t addr, int32_t val);
int evoos_hw_install_guest(int slot, const uint8_t *code, uint64_t n_code, const char *name,
                           const int32_t *init_off, const int32_t *init_val, int n_init);
int32_t evoos_hw_word(int slot, int off);
void supervisor_main(void);

/* Firmware I/O hook. No scheduling. */
void evoos_firmware_after_runctx(int32_t cid, int32_t ran, int32_t fault, int32_t vector);

#endif
