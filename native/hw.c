#include "hw.h"

#include <stddef.h>
#include <stdint.h>

HwMachine hw;

extern void isr_0(void), isr_1(void), isr_2(void), isr_3(void), isr_4(void), isr_5(void), isr_6(void),
    isr_7(void), isr_8(void), isr_9(void), isr_10(void), isr_11(void), isr_12(void), isr_13(void),
    isr_14(void), isr_15(void), isr_16(void), isr_17(void), isr_18(void), isr_19(void), isr_20(void),
    isr_21(void), isr_22(void), isr_23(void), isr_24(void), isr_25(void), isr_26(void), isr_27(void),
    isr_28(void), isr_29(void), isr_30(void), isr_31(void), isr_32(void), isr_128(void);
extern void hw_runctx_resume(void);
extern int32_t hw_ran;

static uint64_t g_hhdm, g_kphys, g_kvirt, g_kcr3;
static int hw_current_cid;
static uint64_t hw_tick_start;

typedef struct {
    uint32_t rax, rbx, rcx, rdx, rsi, rdi, rbp;
    uint32_t r8, r9, r10, r11, r12, r13, r14, r15;
} TrapGpr;

/* Stack layout from isr_common (low → high):
   rax rcx rdx rbx rbp rsi rdi r8-r15, vec, err, rip, cs, rflags, rsp, ss */
typedef struct {
    uint64_t rax, rcx, rdx, rbx, rbp, rsi, rdi;
    uint64_t r8, r9, r10, r11, r12, r13, r14, r15;
    uint64_t vec, err, rip, cs, rflags, rsp, ss;
} TrapFrame;

static inline void outb(uint16_t port, uint8_t val) {
    __asm__ volatile("outb %0, %1" : : "a"(val), "Nd"(port));
}
static inline uint8_t inb(uint16_t port) {
    uint8_t r;
    __asm__ volatile("inb %1, %0" : "=a"(r) : "Nd"(port));
    return r;
}
static inline void outw(uint16_t port, uint16_t val) {
    __asm__ volatile("outw %0, %1" : : "a"(val), "Nd"(port));
}
static inline uint16_t inw(uint16_t port) {
    uint16_t r;
    __asm__ volatile("inw %1, %0" : "=a"(r) : "Nd"(port));
    return r;
}
static inline void wcr3(uint64_t v) { __asm__ volatile("mov %0, %%cr3" : : "r"(v) : "memory"); }
static inline uint64_t rcr3(void) {
    uint64_t v;
    __asm__ volatile("mov %%cr3, %0" : "=r"(v));
    return v;
}
static inline uint64_t rcr2(void) {
    uint64_t v;
    __asm__ volatile("mov %%cr2, %0" : "=r"(v));
    return v;
}

#define PTE_P 1ULL
#define PTE_W 2ULL
#define PTE_U 4ULL
#define PTE_NX (1ULL << 63)

#define NFRAMES 128
static uint8_t frames[NFRAMES][4096] __attribute__((aligned(4096)));
static int nframes;
static uint8_t int_stack[8192] __attribute__((aligned(16)));
uint64_t hw_enter_gpr[16];
uint64_t hw_enter_iret[5];
static uint8_t code_pages[HW_N_GUESTS][4096] __attribute__((aligned(4096)));
static uint8_t stack_pages[HW_N_GUESTS][4096] __attribute__((aligned(4096)));

static uint8_t gdt_blob[64] __attribute__((aligned(16)));
static uint8_t tss[104] __attribute__((aligned(16)));
static uint8_t idt[256 * 16] __attribute__((aligned(16)));

static uint64_t v2p(const void *v) { return (uint64_t)v - g_kvirt + g_kphys; }
static void *p2v_hhdm(uint64_t p) { return (void *)(p + g_hhdm); }

static void *alloc_page(void) {
    void *p;
    int i;
    if (nframes >= NFRAMES) {
        for (;;) {
        }
    }
    p = frames[nframes++];
    for (i = 0; i < 4096; i++) {
        ((uint8_t *)p)[i] = 0;
    }
    return p;
}

struct __attribute__((packed)) gdtr {
    uint16_t lim;
    uint64_t base;
};

static void gdt_set_u64(int i, uint64_t v) {
    uint64_t *g = (uint64_t *)gdt_blob;
    g[i] = v;
}

static void load_gdt_tss(void) {
    uint64_t tss_addr = (uint64_t)(uintptr_t)tss;
    uint64_t tss_lo, tss_hi;
    struct gdtr r;
    int i;
    for (i = 0; i < 104; i++) {
        tss[i] = 0;
    }
    /* RSP0 */
    {
        uint64_t rsp0 = (uint64_t)(uintptr_t)(int_stack + sizeof int_stack);
        tss[4] = (uint8_t)rsp0;
        tss[5] = (uint8_t)(rsp0 >> 8);
        tss[6] = (uint8_t)(rsp0 >> 16);
        tss[7] = (uint8_t)(rsp0 >> 24);
        tss[8] = (uint8_t)(rsp0 >> 32);
        tss[9] = (uint8_t)(rsp0 >> 40);
        tss[10] = (uint8_t)(rsp0 >> 48);
        tss[11] = (uint8_t)(rsp0 >> 56);
        tss[102] = 0x68; /* iopb past end */
        tss[103] = 0;
    }
    gdt_set_u64(0, 0);
    gdt_set_u64(1, 0x00209A0000000000ULL); /* kernel CS */
    gdt_set_u64(2, 0x0000920000000000ULL); /* kernel DS */
    gdt_set_u64(3, 0x0020FA0000000000ULL); /* user CS */
    gdt_set_u64(4, 0x0000F20000000000ULL); /* user DS */
    tss_lo = 0x0000890000000068ULL | ((tss_addr & 0xFFFFFF) << 16) | (((tss_addr >> 24) & 0xFF) << 56);
    tss_hi = tss_addr >> 32;
    gdt_set_u64(5, tss_lo);
    gdt_set_u64(6, tss_hi);
    r.lim = 7 * 8 - 1;
    r.base = (uint64_t)(uintptr_t)gdt_blob;
    __asm__ volatile("lgdt %0" : : "m"(r));
    __asm__ volatile(
        "pushq %[cs]\n\t"
        "leaq 1f(%%rip), %%rax\n\t"
        "pushq %%rax\n\t"
        "lretq\n\t"
        "1:\n\t"
        "mov %[ds], %%ax\n\t"
        "mov %%ax, %%ds\n\t"
        "mov %%ax, %%es\n\t"
        "mov %%ax, %%ss\n\t"
        "mov %%ax, %%fs\n\t"
        "mov %%ax, %%gs\n\t"
        :
        : [cs] "i"(HW_SEL_KCODE), [ds] "i"(HW_SEL_KDATA)
        : "rax", "ax", "memory");
    __asm__ volatile("ltr %0" : : "r"((uint16_t)HW_SEL_TSS));
}

static void idt_set(int vec, void *handler, uint8_t type) {
    uint64_t off = (uint64_t)(uintptr_t)handler;
    uint8_t *e = idt + vec * 16;
    uint16_t sel = HW_SEL_KCODE;
    e[0] = (uint8_t)off;
    e[1] = (uint8_t)(off >> 8);
    e[2] = (uint8_t)sel;
    e[3] = (uint8_t)(sel >> 8);
    e[4] = 0; /* IST=0 */
    e[5] = type;
    e[6] = (uint8_t)(off >> 16);
    e[7] = (uint8_t)(off >> 24);
    e[8] = (uint8_t)(off >> 32);
    e[9] = (uint8_t)(off >> 40);
    e[10] = (uint8_t)(off >> 48);
    e[11] = (uint8_t)(off >> 56);
    e[12] = e[13] = e[14] = e[15] = 0;
}

static void load_idt(void) {
    static void *const stubs[32] = {
        isr_0,  isr_1,  isr_2,  isr_3,  isr_4,  isr_5,  isr_6,  isr_7,  isr_8,  isr_9,  isr_10, isr_11,
        isr_12, isr_13, isr_14, isr_15, isr_16, isr_17, isr_18, isr_19, isr_20, isr_21, isr_22, isr_23,
        isr_24, isr_25, isr_26, isr_27, isr_28, isr_29, isr_30, isr_31,
    };
    struct gdtr r;
    int i;
    for (i = 0; i < 256; i++) {
        idt_set(i, isr_31, 0x8E); /* default unused → #UD-like hang via vec 31 handler */
    }
    for (i = 0; i < 32; i++) {
        idt_set(i, stubs[i], 0x8E);
    }
    idt_set(32, isr_32, 0x8E);
    idt_set(0x80, isr_128, 0xEE); /* DPL 3 */
    r.lim = 256 * 16 - 1;
    r.base = (uint64_t)(uintptr_t)idt;
    __asm__ volatile("lidt %0" : : "m"(r));
}

static void pic_init(void) {
    outb(0x20, 0x11);
    outb(0xA0, 0x11);
    outb(0x21, 0x20);
    outb(0xA1, 0x28);
    outb(0x21, 0x04);
    outb(0xA1, 0x02);
    outb(0x21, 0x01);
    outb(0xA1, 0x01);
    outb(0x21, 0xFE); /* IRQ0 only */
    outb(0xA1, 0xFF);
}

static void pit_oneshot(uint32_t counts) {
    if (counts < 20) {
        counts = 20;
    }
    if (counts > 65535) {
        counts = 65535;
    }
    outb(0x43, 0x30); /* ch0, lobyte/hibyte, mode 0 */
    outb(0x40, (uint8_t)counts);
    outb(0x40, (uint8_t)(counts >> 8));
}

static void pit_stop(void) {
    outb(0x43, 0x30);
    outb(0x40, 0xFF);
    outb(0x40, 0xFF);
}

static uint64_t *pt_walk_alloc(uint64_t *pml4, uint64_t va, uint64_t flags) {
    int i4 = (int)((va >> 39) & 511);
    int i3 = (int)((va >> 30) & 511);
    int i2 = (int)((va >> 21) & 511);
    int i1 = (int)((va >> 12) & 511);
    uint64_t *pdpt, *pd, *pt;
    if (!(pml4[i4] & PTE_P)) {
        pdpt = alloc_page();
        pml4[i4] = v2p(pdpt) | PTE_P | PTE_W | PTE_U;
    } else {
        pdpt = (uint64_t *)((pml4[i4] & 0x000ffffffffff000ULL) - g_kphys + g_kvirt);
        /* kernel-cloned entries point at HHDM-backed pages */
        if ((uint64_t)(uintptr_t)pdpt < 0xffff800000000000ULL && (pml4[i4] & PTE_U) == 0) {
            pdpt = p2v_hhdm(pml4[i4] & 0x000ffffffffff000ULL);
        }
    }
    if (!(pdpt[i3] & PTE_P)) {
        pd = alloc_page();
        pdpt[i3] = v2p(pd) | PTE_P | PTE_W | PTE_U;
    } else {
        pd = (uint64_t *)((pdpt[i3] & 0x000ffffffffff000ULL) - g_kphys + g_kvirt);
        if ((uint64_t)(uintptr_t)pd < 0xffff800000000000ULL && (pdpt[i3] & PTE_U) == 0) {
            pd = p2v_hhdm(pdpt[i3] & 0x000ffffffffff000ULL);
        }
    }
    if (!(pd[i2] & PTE_P)) {
        pt = alloc_page();
        pd[i2] = v2p(pt) | PTE_P | PTE_W | PTE_U;
    } else {
        pt = (uint64_t *)((pd[i2] & 0x000ffffffffff000ULL) - g_kphys + g_kvirt);
        if ((uint64_t)(uintptr_t)pt < 0xffff800000000000ULL && (pd[i2] & PTE_U) == 0) {
            pt = p2v_hhdm(pd[i2] & 0x000ffffffffff000ULL);
        }
    }
    pt[i1] = flags;
    return pt;
}

static void map_user(uint64_t *pml4, uint64_t va, uint64_t phys, uint64_t flags) {
    pt_walk_alloc(pml4, va, (phys & 0x000ffffffffff000ULL) | flags);
}

static void unmap_user_data(uint64_t *pml4, int slot) {
    uint64_t va = HW_DATA_VA_SLOT(slot);
    /* leave PTE not-present; walk without creating if missing */
    int i4 = (int)((va >> 39) & 511);
    int i3 = (int)((va >> 30) & 511);
    int i2 = (int)((va >> 21) & 511);
    int i1 = (int)((va >> 12) & 511);
    uint64_t *pdpt, *pd, *pt;
    if (!(pml4[i4] & PTE_P)) {
        return;
    }
    pdpt = (uint64_t *)((pml4[i4] & 0x000ffffffffff000ULL) - g_kphys + g_kvirt);
    if ((uint64_t)(uintptr_t)pdpt < g_kvirt) {
        pdpt = p2v_hhdm(pml4[i4] & 0x000ffffffffff000ULL);
    }
    if (!(pdpt[i3] & PTE_P)) {
        return;
    }
    pd = (uint64_t *)((pdpt[i3] & 0x000ffffffffff000ULL) - g_kphys + g_kvirt);
    if ((uint64_t)(uintptr_t)pd < g_kvirt) {
        pd = p2v_hhdm(pdpt[i3] & 0x000ffffffffff000ULL);
    }
    if (!(pd[i2] & PTE_P)) {
        return;
    }
    pt = (uint64_t *)((pd[i2] & 0x000ffffffffff000ULL) - g_kphys + g_kvirt);
    if ((uint64_t)(uintptr_t)pt < g_kvirt) {
        pt = p2v_hhdm(pd[i2] & 0x000ffffffffff000ULL);
    }
    pt[i1] = 0;
}

static uint64_t *clone_kernel_pml4(void) {
    uint64_t *src = p2v_hhdm(g_kcr3 & 0x000ffffffffff000ULL);
    uint64_t *dst = alloc_page();
    int i;
    for (i = 0; i < 512; i++) {
        dst[i] = src[i];
    }
    return dst;
}

static int slot_of_word(int32_t addr) {
    int s;
    for (s = 0; s < HW_N_GUESTS; s++) {
        if (addr >= HW_SLOT_BASE(s) && addr < HW_SLOT_BOUND(s)) {
            return s;
        }
    }
    return -1;
}

static void apply_prot_maps(int cid) {
    HwContext *c = &hw.ctx[cid];
    uint64_t *pml4 = (uint64_t *)(c->cr3 - g_kphys + g_kvirt);
    int s, r;
    for (s = 0; s < HW_N_GUESTS; s++) {
        unmap_user_data(pml4, s);
    }
    for (r = 0; r < c->n_prot; r++) {
        int32_t lo = c->prot_lo[r], hi = c->prot_hi[r];
        for (s = 0; s < HW_N_GUESTS; s++) {
            int32_t b = HW_SLOT_BASE(s), e = HW_SLOT_BOUND(s);
            if (lo < e && hi > b) {
                map_user(pml4, HW_DATA_VA_SLOT(s), v2p(hw.slot_page[s]), PTE_P | PTE_W | PTE_U | PTE_NX);
            }
        }
    }
}

int evoos_hw_addprot(int32_t cid, int32_t lo, int32_t hi) {
    HwContext *c;
    if (cid <= 0 || cid >= hw.n_contexts) {
        return -1;
    }
    c = &hw.ctx[cid];
    if (c->n_prot >= HW_MAX_PROT) {
        return HW_FAULT_PROT_FULL;
    }
    c->prot_lo[c->n_prot] = lo;
    c->prot_hi[c->n_prot] = hi;
    c->n_prot++;
    apply_prot_maps(cid);
    return 0;
}

void evoos_hw_clrprot(int32_t cid) {
    if (cid <= 0 || cid >= hw.n_contexts) {
        return;
    }
    hw.ctx[cid].n_prot = 0;
    apply_prot_maps(cid);
}

int32_t evoos_hw_ctxget(int32_t cid, int32_t field) {
    HwContext *c;
    if (cid < 0 || cid >= hw.n_contexts) {
        cid = 0;
    }
    c = &hw.ctx[cid];
    switch (field) {
    case HW_FLD_PC:
        return (int32_t)c->rip;
    case HW_FLD_HALTED:
        return c->halted;
    case HW_FLD_FAULT:
        return c->fault;
    case HW_FLD_CYCLES:
        return c->cycles;
    case HW_FLD_DATA_BASE:
        return c->data_base;
    case HW_FLD_DATA_BOUND:
        return c->data_bound;
    case HW_FLD_N_CONTEXTS:
        return hw.n_contexts;
    case HW_FLD_STATUS:
        return (c->halted) | ((c->fault != 0) << 1);
    case HW_FLD_LAST_RUN_STEPS:
        return hw.last_run_steps;
    case HW_FLD_LAST_RUN_FAULT:
        return hw.last_run_fault;
    case HW_FLD_LAST_RUN_CTX:
        return hw.last_run_ctx;
    case HW_FLD_LAST_BLK_STATUS:
        return hw.last_blk_status;
    case HW_FLD_N_BLOCKS:
        return hw.n_blocks;
    case HW_FLD_BLK_WORDS:
        return HW_BLK_WORDS;
    case HW_FLD_LAST_SVC:
        return hw.last_svc;
    case HW_FLD_LAST_SVC_CTX:
        return hw.last_svc_ctx;
    default:
        return 0;
    }
}

void evoos_hw_ctxset(int32_t cid, int32_t field, int32_t val) {
    HwContext *c;
    if (cid < 0 || cid >= hw.n_contexts) {
        return;
    }
    c = &hw.ctx[cid];
    if (field == HW_FLD_PC) {
        c->rip = (uint64_t)(uint32_t)val;
    } else if (field == HW_FLD_HALTED) {
        c->halted = val;
    } else if (field == HW_FLD_FAULT) {
        c->fault = val;
    } else if (field == HW_FLD_LAST_SVC) {
        hw.last_svc = val;
    }
}

int32_t evoos_hw_load(int32_t addr) {
    int s = slot_of_word(addr);
    if (s >= 0) {
        return ((int32_t *)hw.slot_page[s])[addr - HW_SLOT_BASE(s)];
    }
    if (addr >= 0 && addr < HW_MEM_WORDS) {
        return hw.extra[addr];
    }
    return 0;
}

void evoos_hw_store(int32_t addr, int32_t val) {
    int s = slot_of_word(addr);
    if (s >= 0) {
        ((int32_t *)hw.slot_page[s])[addr - HW_SLOT_BASE(s)] = val;
        return;
    }
    if (addr >= 0 && addr < HW_MEM_WORDS) {
        hw.extra[addr] = val;
    }
}

static int ata_ready(void) {
    int i;
    for (i = 0; i < 100000; i++) {
        uint8_t s = inb(0x1F7);
        if (s == 0xFF) {
            return -1;
        }
        if ((s & 0x80) == 0) {
            return 0;
        }
    }
    return -1;
}

static int ata_wait_drq(void) {
    int i;
    for (i = 0; i < 100000; i++) {
        uint8_t s = inb(0x1F7);
        if (s == 0xFF) {
            return -1;
        }
        if (s & 1) {
            return -1;
        }
        if (s & 0x08) {
            return 0;
        }
    }
    return -1;
}

static int ata_xfer_lba(int32_t lba, uint8_t *buf, int wr, int32_t max_lba) {
    int i;
    if (!hw.has_disk || lba < 0 || lba >= max_lba) {
        return 2;
    }
    if (ata_ready() < 0) {
        return 1;
    }
    outb(0x1F2, 1);
    outb(0x1F3, (uint8_t)lba);
    outb(0x1F4, (uint8_t)(lba >> 8));
    outb(0x1F5, (uint8_t)(lba >> 16));
    outb(0x1F6, (uint8_t)(0xE0 | ((lba >> 24) & 0x0F)));
    outb(0x1F7, wr ? 0x30 : 0x20);
    if (ata_wait_drq() < 0) {
        return 1;
    }
    if (wr) {
        for (i = 0; i < 256; i++) {
            uint16_t w = (uint16_t)buf[i * 2] | ((uint16_t)buf[i * 2 + 1] << 8);
            outw(0x1F0, w);
        }
        if (ata_ready() < 0) {
            return 1;
        }
    } else {
        for (i = 0; i < 256; i++) {
            uint16_t w = inw(0x1F0);
            buf[i * 2] = (uint8_t)w;
            buf[i * 2 + 1] = (uint8_t)(w >> 8);
        }
    }
    return 0;
}

static int ata_xfer(int32_t blk, uint8_t *buf, int wr) {
    return ata_xfer_lba(blk, buf, wr, HW_N_BLOCKS);
}

int evoos_hw_ata_lba(int32_t lba, uint8_t *buf, int wr) {
    /* Firmware meta archive. Guest BLK* stays on LBAs 0..31. */
    return ata_xfer_lba(lba, buf, wr, 2048);
}

int32_t evoos_hw_blkrd(int32_t blk, int32_t dest) {
    uint8_t sec[512];
    int i, st;
    for (i = 0; i < 512; i++) {
        sec[i] = 0;
    }
    st = ata_xfer(blk, sec, 0);
    hw.last_blk_status = st;
    if (st == 0) {
        for (i = 0; i < HW_BLK_WORDS; i++) {
            int32_t w = (int32_t)((uint32_t)sec[i * 4] | ((uint32_t)sec[i * 4 + 1] << 8) |
                                  ((uint32_t)sec[i * 4 + 2] << 16) | ((uint32_t)sec[i * 4 + 3] << 24));
            evoos_hw_store(dest + i, w);
        }
    }
    return st;
}

void evoos_hw_blkwr(int32_t blk, int32_t src) {
    uint8_t sec[512];
    int i;
    for (i = 0; i < 512; i++) {
        sec[i] = 0;
    }
    for (i = 0; i < HW_BLK_WORDS; i++) {
        uint32_t w = (uint32_t)evoos_hw_load(src + i);
        sec[i * 4] = (uint8_t)w;
        sec[i * 4 + 1] = (uint8_t)(w >> 8);
        sec[i * 4 + 2] = (uint8_t)(w >> 16);
        sec[i * 4 + 3] = (uint8_t)(w >> 24);
    }
    hw.last_blk_status = ata_xfer(blk, sec, 1);
}

void evoos_hw_svc(int32_t code) {
    hw.last_svc = code;
    hw.last_svc_ctx = 0;
}

int32_t evoos_hw_word(int slot, int off) {
    if (slot < 0 || slot >= HW_N_GUESTS) {
        return 0;
    }
    return ((int32_t *)hw.slot_page[slot])[off];
}

int evoos_hw_install_guest(int slot, const uint8_t *code, uint64_t n_code, const char *name,
                           const int32_t *init_off, const int32_t *init_val, int n_init) {
    HwContext *c;
    uint64_t *pml4;
    int i, cid;
    if (slot < 0 || slot >= HW_N_GUESTS) {
        return -1;
    }
    cid = slot + 1;
    c = &hw.ctx[cid];
    if (n_code > 4096) {
        n_code = 4096;
    }
    for (i = 0; i < 4096; i++) {
        code_pages[slot][i] = 0;
        stack_pages[slot][i] = 0;
        hw.slot_page[slot][i] = 0;
    }
    for (i = 0; (uint64_t)i < n_code; i++) {
        code_pages[slot][i] = code[i];
    }
    for (i = 0; i < n_init; i++) {
        int off = init_off[i];
        if (off >= 0 && off < 1024) {
            ((int32_t *)hw.slot_page[slot])[off] = init_val[i];
        }
    }
    c->code_page = code_pages[slot];
    c->stack_page = stack_pages[slot];
    c->data_page = hw.slot_page[slot];
    c->data_base = HW_SLOT_BASE(slot);
    c->data_bound = HW_SLOT_BOUND(slot);
    c->halted = 0;
    c->fault = 0;
    c->cycles = 0;
    c->preempts = 0;
    c->syscalls = 0;
    c->rip = HW_CODE_VA;
    c->rsp = HW_STACK_VA;
    c->rflags = 0x202;
    c->rdi = HW_DATA_VA_SLOT(slot);
    c->rsi = HW_DATA_VA_SLOT(slot == 0 ? 1 : 0);
    c->rax = c->rbx = c->rcx = c->rdx = c->rbp = 0;
    c->r8 = c->r9 = c->r10 = c->r11 = c->r12 = c->r13 = c->r14 = c->r15 = 0;
    c->last_vector = 0;
    c->last_cr2 = 0;
    for (i = 0; i < 31 && name && name[i]; i++) {
        c->name[i] = name[i];
    }
    c->name[i] = 0;
    pml4 = (uint64_t *)(c->cr3 - g_kphys + g_kvirt);
    map_user(pml4, HW_CODE_VA, v2p(code_pages[slot]), PTE_P | PTE_U);
    map_user(pml4, HW_STACK_PAGE, v2p(stack_pages[slot]), PTE_P | PTE_W | PTE_U | PTE_NX);
    return 0;
}

static uint32_t pit_counts_for(int32_t n_steps) {
    int64_t c = (int64_t)n_steps * 80;
    if (c < 80) {
        c = 80;
    }
    if (c > 20000) {
        c = 20000;
    }
    return (uint32_t)c;
}

void evoos_hw_runctx_enter(int32_t cid, int32_t n_steps) {
    HwContext *c;
    if (cid <= 0 || cid >= hw.n_contexts) {
        hw_ran = 0;
        return;
    }
    c = &hw.ctx[cid];
    if (c->halted || c->fault) {
        hw_ran = 0;
        hw.last_run_steps = 0;
        hw.last_run_fault = c->fault;
        hw.last_run_ctx = cid;
        hw.switch_count++;
        return;
    }
    apply_prot_maps(cid);
    hw_current_cid = cid;
    hw_tick_start = hw.cycles;
    hw.switch_count++;
    pit_oneshot(pit_counts_for(n_steps));
    outb(0x21, 0xFE); /* unmask IRQ0 */
    __asm__ volatile("cli");
    {
        hw_enter_gpr[0] = c->rax;
        hw_enter_gpr[1] = c->rbx;
        hw_enter_gpr[2] = c->rcx;
        hw_enter_gpr[3] = c->rdx;
        hw_enter_gpr[4] = c->rsi;
        hw_enter_gpr[5] = c->rdi;
        hw_enter_gpr[6] = c->rbp;
        hw_enter_gpr[7] = c->r8;
        hw_enter_gpr[8] = c->r9;
        hw_enter_gpr[9] = c->r10;
        hw_enter_gpr[10] = c->r11;
        hw_enter_gpr[11] = c->r12;
        hw_enter_gpr[12] = c->r13;
        hw_enter_gpr[13] = c->r14;
        hw_enter_gpr[14] = c->r15;
        hw_enter_gpr[15] = c->cr3;
        hw_enter_iret[0] = c->rip;
        hw_enter_iret[1] = HW_SEL_UCODE;
        hw_enter_iret[2] = c->rflags | 0x200;
        hw_enter_iret[3] = c->rsp;
        hw_enter_iret[4] = HW_SEL_UDATA;
        __asm__ volatile(
            "leaq hw_enter_gpr(%%rip), %%r10\n\t"
            "movq 120(%%r10), %%rcx\n\t"
            "movq %%rcx, %%cr3\n\t"
            "movq 8(%%r10), %%rbx\n\t"
            "movq 16(%%r10), %%rcx\n\t"
            "movq 24(%%r10), %%rdx\n\t"
            "movq 32(%%r10), %%rsi\n\t"
            "movq 40(%%r10), %%rdi\n\t"
            "movq 48(%%r10), %%rbp\n\t"
            "movq 56(%%r10), %%r8\n\t"
            "movq 64(%%r10), %%r9\n\t"
            "movq 80(%%r10), %%r11\n\t"
            "movq 88(%%r10), %%r12\n\t"
            "movq 96(%%r10), %%r13\n\t"
            "movq 104(%%r10), %%r14\n\t"
            "movq 112(%%r10), %%r15\n\t"
            "movq 0(%%r10), %%rax\n\t"
            "leaq hw_enter_iret(%%rip), %%rsp\n\t"
            "movq 72(%%r10), %%r10\n\t"
            "iretq\n\t"
            :
            :
            : "memory", "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "r8", "r9", "r10", "r11",
              "r12", "r13", "r14", "r15");
    }
    __builtin_unreachable();
}

void evoos_hw_trap(TrapFrame *f) {
    HwContext *c;
    int32_t cid = hw_current_cid;
    int32_t ran;
    int user = ((f->cs & 3) == 3);
    if (!user) {
        for (;;) {
            __asm__ volatile("hlt");
        }
    }
    pit_stop();
    outb(0x21, 0xFF);
    outb(0x20, 0x20);
    c = &hw.ctx[cid];
    c->rax = f->rax;
    c->rcx = f->rcx;
    c->rdx = f->rdx;
    c->rbx = f->rbx;
    c->rbp = f->rbp;
    c->rsi = f->rsi;
    c->rdi = f->rdi;
    c->r8 = f->r8;
    c->r9 = f->r9;
    c->r10 = f->r10;
    c->r11 = f->r11;
    c->r12 = f->r12;
    c->r13 = f->r13;
    c->r14 = f->r14;
    c->r15 = f->r15;
    c->rip = f->rip;
    c->rsp = f->rsp;
    c->rflags = f->rflags;
    c->last_vector = (int32_t)f->vec;
    c->last_cr2 = rcr2();
    ran = 1;
    if (f->vec == HW_VEC_HALT) {
        c->syscalls++;
        c->rip = f->rip; /* after int */
        if (f->rax == 1 || f->rax == 2) {
            hw.last_svc = (int32_t)f->rax;
            hw.last_svc_ctx = cid;
            ran = 8;
        } else {
            c->halted = 1;
            ran = 8;
        }
    } else if (f->vec == HW_VEC_TIMER) {
        c->preempts++;
        ran = 16;
    } else if (f->vec == HW_VEC_PF || f->vec == HW_VEC_GP) {
        /* Isolation is the MMU: #PF means the write did not land. */
        c->fault = HW_FAULT_PROT;
        ran = 1;
    } else {
        c->fault = HW_FAULT_BAD_OP;
    }
    c->cycles += ran;
    hw.cycles += ran;
    hw.last_run_steps = ran;
    hw.last_run_fault = c->fault;
    hw.last_run_ctx = cid;
    hw_ran = ran;
    wcr3(g_kcr3);
    evoos_firmware_after_runctx(cid, ran, c->fault, (int32_t)f->vec);
    __asm__ volatile("jmp hw_runctx_resume");
}

void evoos_hw_init(uint64_t hhdm, uint64_t kphys, uint64_t kvirt) {
    int s;
    g_hhdm = hhdm;
    g_kphys = kphys;
    g_kvirt = kvirt;
    g_kcr3 = rcr3();
    hw.n_contexts = HW_N_CTX;
    hw.io_base = 1920;
    hw.n_blocks = HW_N_BLOCKS;
    hw.last_svc = 0;
    hw.last_svc_ctx = 0;
    hw.last_blk_status = 0;
    {
        uint8_t sec[512];
        int i;
        for (i = 0; i < 512; i++) {
            sec[i] = 0;
        }
        hw.has_disk = 1;
        if (ata_xfer(0, sec, 0) != 0) {
            hw.has_disk = 0;
        }
    }
    for (s = 0; s < HW_N_CTX; s++) {
        uint64_t *pml4 = clone_kernel_pml4();
        hw.ctx[s].cr3 = v2p(pml4);
        hw.ctx[s].rflags = 0x202;
        hw.ctx[s].halted = s == 0 ? 0 : 1;
    }
    hw.ctx[0].data_base = 0;
    hw.ctx[0].data_bound = HW_MEM_WORDS;
    for (s = 0; s < HW_N_GUESTS; s++) {
        hw.ctx[s + 1].data_base = HW_SLOT_BASE(s);
        hw.ctx[s + 1].data_bound = HW_SLOT_BOUND(s);
        hw.ctx[s + 1].halted = 1;
    }
    load_gdt_tss();
    load_idt();
    pic_init();
    outb(0x21, 0xFF); /* IRQs masked in ring 0; guest rflags.IF enables IRQ0 */
}

/* Default hook if firmware does not provide one. */
void evoos_firmware_after_runctx(int32_t cid, int32_t ran, int32_t fault, int32_t vector)
    __attribute__((weak));
void evoos_firmware_after_runctx(int32_t cid, int32_t ran, int32_t fault, int32_t vector) {
    (void)cid;
    (void)ran;
    (void)fault;
    (void)vector;
}
