#include "evoos_meta.h"

#include "evoos_vm.h"
#include "guest_catalogue.h"
#include "hw.h"
#include "supervisor_seed.h"

#include <stddef.h>
#include <stdint.h>

#define META_KIND_SUM 0
#define META_KIND_FILL 1
#define META_KIND_XOR 2
#define META_KIND_COPY 3
#define META_KIND_MAX 4
#define META_KIND_MAL 5
#define META_KIND_SPIN 6
#define META_KIND_PUT 7
#define META_KIND_GET 8
#define META_KIND_PRESS 9
#define META_KIND_ROUTE 10

typedef struct {
    EvoOSInstr code[META_MAX_CODE];
    int32_t n;
    int32_t id;
    int32_t parent;
    int32_t fit;
    char origin[24];
} MetaCand;

typedef struct {
    int32_t kind; /* 1 spawn, 2 svc */
    int32_t slot;
    int32_t aux;
} MetaTrace;

static MetaCand pop[META_POP];
static MetaCand incumbent;
static MetaCand rollback;
static MetaTrace traces[META_TRACE];
static int32_t n_trace;
static int32_t tr_i;
static uint32_t rng_s;
static int32_t generation;
static int32_t evals;
static int32_t swaps;
static int32_t rollbacks;
static int32_t evo_on = 1;
static int32_t resumed;
static int32_t next_id = 3;
static int32_t live_pc;
static uint32_t live_r[8];
static int32_t live_fit;
static int32_t last_shadow;
static int32_t iso_at_swap;
static int32_t swapped_live;
static int32_t lineage[8];
static int32_t lineage_n;
static EvoOSMachine shadow;
static int32_t tick_div;

static void mcpy(void *d, const void *s, size_t n) {
    uint8_t *dd = (uint8_t *)d;
    const uint8_t *ss = (const uint8_t *)s;
    while (n--) {
        *dd++ = *ss++;
    }
}

static void mset(void *d, int c, size_t n) {
    uint8_t *dd = (uint8_t *)d;
    while (n--) {
        *dd++ = (uint8_t)c;
    }
}

static int scmp(const char *a, const char *b) {
    while (*a && *b && *a == *b) {
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

static void scpy(char *d, const char *s, int max) {
    int i;
    for (i = 0; i < max - 1 && s[i]; i++) {
        d[i] = s[i];
    }
    d[i] = 0;
}

static uint32_t rnd(void) {
    rng_s ^= rng_s << 13;
    rng_s ^= rng_s >> 17;
    rng_s ^= rng_s << 5;
    return rng_s;
}

static int32_t rndn(int32_t n) {
    if (n <= 1) {
        return 0;
    }
    return (int32_t)(rnd() % (uint32_t)n);
}

static void log_s(const char *s) { evoos_firmware_log(s); }
static void log_i(int32_t v) { evoos_firmware_log_i32(v); }

static void cand_from(MetaCand *c, const EvoOSInstr *src, int n, int32_t id, int32_t parent,
                      const char *origin) {
    int i;
    mset(c, 0, sizeof(*c));
    if (n > META_MAX_CODE) {
        n = META_MAX_CODE;
    }
    c->n = n;
    c->id = id;
    c->parent = parent;
    for (i = 0; i < n; i++) {
        c->code[i] = src[i];
    }
    scpy(c->origin, origin, 24);
}

static int32_t kind_id(const char *kind) {
    if (scmp(kind, "sum")) {
        return META_KIND_SUM;
    }
    if (scmp(kind, "fill")) {
        return META_KIND_FILL;
    }
    if (scmp(kind, "xor")) {
        return META_KIND_XOR;
    }
    if (scmp(kind, "copyinc")) {
        return META_KIND_COPY;
    }
    if (scmp(kind, "max")) {
        return META_KIND_MAX;
    }
    if (scmp(kind, "malicious")) {
        return META_KIND_MAL;
    }
    if (scmp(kind, "spin")) {
        return META_KIND_SPIN;
    }
    if (scmp(kind, "put")) {
        return META_KIND_PUT;
    }
    if (scmp(kind, "get")) {
        return META_KIND_GET;
    }
    if (scmp(kind, "pressure")) {
        return META_KIND_PRESS;
    }
    if (scmp(kind, "route")) {
        return META_KIND_ROUTE;
    }
    return META_KIND_SUM;
}

static const char *kind_name(int32_t id) {
    switch (id) {
    case META_KIND_FILL:
        return "fill";
    case META_KIND_XOR:
        return "xor";
    case META_KIND_COPY:
        return "copyinc";
    case META_KIND_MAX:
        return "max";
    case META_KIND_MAL:
        return "malicious";
    case META_KIND_SPIN:
        return "spin";
    case META_KIND_PUT:
        return "put";
    case META_KIND_GET:
        return "get";
    case META_KIND_PRESS:
        return "pressure";
    case META_KIND_ROUTE:
        return "route";
    default:
        return "sum";
    }
}

/* ---- mutations (same operators as evoos.agent.mutations, gadgets pack) ---- */

static const int32_t OPS[] = {1,  2,  3,  4,  5,  6,  7,  8,  9,  10, 11, 12, 13, 14, 15, 16, 17,
                              18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34};
#define N_OPS ((int)(sizeof(OPS) / sizeof(OPS[0])))

static EvoOSInstr rand_ins(void) {
    EvoOSInstr in;
    uint32_t roll = rnd() % 100;
    in.op = OPS[rndn(N_OPS)];
    in.a = rndn(8);
    in.b = rndn(8);
    in.c = rndn(8);
    if (roll < 55) {
        in.imm = (int32_t)rndn(41) - 8;
    } else if (roll < 80) {
        static const int32_t imms[] = {0, 1, 2, 3, 4, 5, 8, 16, 32, 64, 256, 384, 512, 640};
        in.imm = imms[rndn(14)];
    } else {
        in.imm = (int32_t)rndn(193) - 64;
    }
    return in;
}

static void insert_at(MetaCand *c, int i, const EvoOSInstr *ins, int k) {
    int j;
    if (i < 0) {
        i = 0;
    }
    if (i > c->n) {
        i = c->n;
    }
    if (c->n + k > META_MAX_CODE) {
        k = META_MAX_CODE - c->n;
    }
    if (k <= 0) {
        return;
    }
    for (j = c->n - 1; j >= i; j--) {
        c->code[j + k] = c->code[j];
    }
    for (j = 0; j < k; j++) {
        c->code[i + j] = ins[j];
    }
    c->n += k;
}

static void mutate(MetaCand *dst, const MetaCand *parent, const MetaCand *other) {
    int op, i, j, n;
    EvoOSInstr tmp[5];
    *dst = *parent;
    dst->parent = parent->id;
    dst->id = next_id++;
    dst->fit = 0;
    n = dst->n;
    op = rndn(12);
    if (op == 0 || n == 0) {
        tmp[0] = rand_ins();
        insert_at(dst, n ? rndn(n + 1) : 0, tmp, 1);
        scpy(dst->origin, "insert", 24);
    } else if (op == 1 && n > 1) {
        i = rndn(n);
        for (j = i; j < n - 1; j++) {
            dst->code[j] = dst->code[j + 1];
        }
        dst->n--;
        scpy(dst->origin, "remove", 24);
    } else if (op == 2 && n) {
        i = rndn(n);
        dst->code[i].op = OPS[rndn(N_OPS)];
        scpy(dst->origin, "modify_op", 24);
    } else if (op == 3 && n) {
        i = rndn(n);
        j = rndn(3);
        if (j == 0) {
            dst->code[i].a = rndn(8);
        } else if (j == 1) {
            dst->code[i].b = rndn(8);
        } else {
            dst->code[i].c = rndn(8);
        }
        scpy(dst->origin, "modify_operand", 24);
    } else if (op == 4 && n) {
        static const int32_t dlt[] = {-16, -4, -1, 1, 4, 16};
        i = rndn(n);
        dst->code[i].imm += dlt[rndn(6)];
        scpy(dst->origin, "modify_imm", 24);
    } else if (op == 5 && n) {
        i = rndn(n);
        j = i + 1 + rndn(n - i < 6 ? n - i : 6);
        {
            int k, len = j - i;
            if (dst->n + len > META_MAX_CODE) {
                len = META_MAX_CODE - dst->n;
            }
            for (k = 0; k < len; k++) {
                tmp[k % 5] = dst->code[i + k];
            }
            insert_at(dst, rndn(dst->n + 1), dst->code + i, len < 5 ? len : 5);
        }
        scpy(dst->origin, "duplicate_block", 24);
    } else if (op == 6 && n >= 2) {
        EvoOSInstr t;
        i = rndn(n);
        j = rndn(n);
        t = dst->code[i];
        dst->code[i] = dst->code[j];
        dst->code[j] = t;
        scpy(dst->origin, "swap", 24);
    } else if (op == 7) {
        int k = 2 + rndn(4);
        for (i = 0; i < k; i++) {
            tmp[i] = rand_ins();
        }
        insert_at(dst, n ? rndn(n + 1) : 0, tmp, k);
        scpy(dst->origin, "insert_gadget", 24);
    } else if (op == 8 && other && other->n) {
        int ca = n ? rndn(n + 1) : 0;
        int cb = rndn(other->n + 1);
        int nn = 0;
        MetaCand out;
        mset(&out, 0, sizeof(out));
        if (rnd() & 1) {
            for (i = 0; i < ca && nn < META_MAX_CODE; i++) {
                out.code[nn++] = parent->code[i];
            }
            for (i = cb; i < other->n && nn < META_MAX_CODE; i++) {
                out.code[nn++] = other->code[i];
            }
        } else {
            for (i = 0; i < cb && nn < META_MAX_CODE; i++) {
                out.code[nn++] = other->code[i];
            }
            for (i = ca; i < parent->n && nn < META_MAX_CODE; i++) {
                out.code[nn++] = parent->code[i];
            }
        }
        out.n = nn;
        out.id = dst->id;
        out.parent = parent->id;
        scpy(out.origin, "crossover", 24);
        *dst = out;
    } else if (op == 9 && n) {
        static const int32_t cf[] = {17, 18, 19, 20, 21, 22};
        i = rndn(n);
        dst->code[i].op = cf[rndn(6)];
        dst->code[i].imm = rndn(n * 2 + 1) - n;
        scpy(dst->origin, "control_flow", 24);
    } else if (op == 10) {
        /* insert_runctx_gadget */
        tmp[0].op = 3;
        tmp[0].a = 1 + rndn(7);
        tmp[0].b = 0;
        tmp[0].c = 0;
        tmp[0].imm = 1 + rndn(4);
        tmp[1].op = 25;
        tmp[1].a = tmp[0].a;
        tmp[1].b = 0;
        tmp[1].c = 0;
        {
            static const int32_t q[] = {8, 16, 32, 64, 128, 256};
            tmp[1].imm = q[rndn(6)];
        }
        insert_at(dst, n ? rndn(n + 1) : 0, tmp, 2);
        scpy(dst->origin, "insert_runctx_gadget", 24);
    } else {
        tmp[0].op = 3;
        tmp[0].a = 1 + rndn(7);
        tmp[0].b = 0;
        tmp[0].c = 0;
        tmp[0].imm = 1 + rndn(4);
        tmp[1].op = 26;
        tmp[1].a = tmp[0].a == 1 ? 2 : 1;
        tmp[1].b = tmp[0].a;
        tmp[1].c = 0;
        tmp[1].imm = 4;
        tmp[2].op = 26;
        tmp[2].a = tmp[0].a == 3 ? 4 : 3;
        tmp[2].b = tmp[0].a;
        tmp[2].c = 0;
        tmp[2].imm = 5;
        tmp[3].op = 28;
        tmp[3].a = tmp[0].a;
        tmp[3].b = tmp[1].a;
        tmp[3].c = tmp[2].a;
        tmp[3].imm = 0;
        insert_at(dst, n ? rndn(n + 1) : 0, tmp, 4);
        scpy(dst->origin, "insert_mmu_gadget", 24);
    }
    if (dst->n <= 0) {
        dst->code[0].op = 0;
        dst->n = 1;
    }
}

/* ---- shadow eval (C interpreter; never touches live hw.ctx) ---- */

static int install_kind(EvoOSMachine *m, int slot, int32_t kid) {
    const char *k = kind_name(kid);
    int idx = cat_find(k, slot);
    const CatProg *p;
    if (idx < 0) {
        return -1;
    }
    p = &CAT_PROGS[idx];
    return evoos_install_guest(m, slot + 1, p->code, p->n_code, p->data_base, p->data_bound,
                               p->init_addr, p->init_val, p->n_init, p->name);
}

static int32_t shadow_eval(const MetaCand *c, int *safe) {
    int i, spawned = 0, progressed = 0, completed = 0, n_g = 0;
    int32_t fit, f, s, e;
    int32_t useful = 0;
    int has_put = 0, persist_ok = 0;
    int has_route = 0, route_ok = 0;
    *safe = 0;
    evoos_reset_machine(&shadow, 5, 1024, 2500);
    evoos_install_supervisor(&shadow, c->code, c->n);
    {
        int replay = n_trace < 8 ? n_trace : 8;
    for (i = 0; i < replay; i++) {
        int idx = (tr_i - replay + i + META_TRACE) % META_TRACE;
        MetaTrace t = traces[idx];
        if (t.kind == 1) {
            if (install_kind(&shadow, t.slot, t.aux) == 0) {
                spawned++;
                if (t.aux == META_KIND_PUT) {
                    has_put = 1;
                }
                if (t.aux == META_KIND_ROUTE) {
                    has_route = 1;
                }
            }
        } else if (t.kind == 2) {
            shadow.last_svc = t.aux;
            shadow.last_svc_ctx = t.slot + 1;
        }
        evoos_run_slice(&shadow, 280);
        if (shadow.isolation_n > 0) {
            return 0;
        }
    }
    }
    evoos_run_slice(&shadow, 600);
    if (shadow.isolation_n > 0) {
        return 0;
    }
    for (i = 1; i < shadow.n_contexts; i++) {
        EvoOSContext *g = &shadow.contexts[i];
        if (g->n_code <= 0) {
            continue;
        }
        n_g++;
        if (g->cycles > 0 || g->fault || g->halted) {
            progressed++;
        }
        if (g->halted) {
            completed++;
            useful += g->cycles;
        }
    }
    if (n_g == 0) {
        n_g = 1;
    }
    if (shadow.contexts[0].halted && completed < n_g) {
        return 0;
    }
    if (progressed < n_g) {
        return 0;
    }
    *safe = 1;
    f = (800 * completed + 200 * progressed) / n_g;
    s = 1000;
    e = shadow.cycles > 0 ? (1000 * useful) / shadow.cycles : 0;
    if (e > 1000) {
        e = 1000;
    }
    fit = (45 * f + 20 * s + 15 * e) / 80;
    if (has_put) {
        int b;
        for (b = 0; b < EVOOS_N_BLOCKS; b++) {
            if (shadow.disk[b][0] == 123) {
                persist_ok = 1;
                break;
            }
        }
        if (persist_ok) {
            fit += 250;
        }
    }
    if (has_route) {
        int cid;
        for (cid = 1; cid < shadow.n_contexts; cid++) {
            int32_t base = shadow.contexts[cid].data_base;
            if (base >= 0 && base + 2 < shadow.mem_words && shadow.mem[base + 2] == 77 &&
                cid != 1) {
                route_ok = 1;
            }
        }
        if (route_ok) {
            fit += 80;
        }
    }
    if (fit > 1000) {
        fit = 1000;
    }
    (void)spawned;
    return fit;
}

/* ---- ATA archive (firmware LBAs 64+, not guest BLK* namespace) ---- */

static void wr32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int save_code(int32_t lba, const MetaCand *c) {
    uint8_t sec[512];
    int i, w, off, st;
    int nsec = 6;
    for (i = 0; i < nsec; i++) {
        mset(sec, 0, 512);
        if (i == 0) {
            wr32(sec + 0, (uint32_t)c->n);
            wr32(sec + 4, (uint32_t)c->id);
            wr32(sec + 8, (uint32_t)c->parent);
            wr32(sec + 12, (uint32_t)c->fit);
            mcpy(sec + 16, c->origin, 24);
            off = 48;
            w = 0;
        } else {
            off = 0;
        }
        while (off + 20 <= 512 && w < c->n) {
            wr32(sec + off, (uint32_t)c->code[w].op);
            wr32(sec + off + 4, (uint32_t)c->code[w].a);
            wr32(sec + off + 8, (uint32_t)c->code[w].b);
            wr32(sec + off + 12, (uint32_t)c->code[w].c);
            wr32(sec + off + 16, (uint32_t)c->code[w].imm);
            off += 20;
            w++;
        }
        st = evoos_hw_ata_lba(lba + i, sec, 1);
        if (st != 0) {
            return st;
        }
    }
    return 0;
}

static int load_code(int32_t lba, MetaCand *c) {
    uint8_t sec[512];
    int i, w, off, st;
    int nsec = 6;
    mset(c, 0, sizeof(*c));
    for (i = 0; i < nsec; i++) {
        mset(sec, 0, 512);
        st = evoos_hw_ata_lba(lba + i, sec, 0);
        if (st != 0) {
            return st;
        }
        if (i == 0) {
            c->n = (int32_t)rd32(sec + 0);
            c->id = (int32_t)rd32(sec + 4);
            c->parent = (int32_t)rd32(sec + 8);
            c->fit = (int32_t)rd32(sec + 12);
            mcpy(c->origin, sec + 16, 23);
            c->origin[23] = 0;
            if (c->n < 0 || c->n > META_MAX_CODE) {
                return 1;
            }
            off = 48;
            w = 0;
        } else {
            off = 0;
        }
        while (off + 20 <= 512 && w < c->n) {
            c->code[w].op = (int32_t)rd32(sec + off);
            c->code[w].a = (int32_t)rd32(sec + off + 4);
            c->code[w].b = (int32_t)rd32(sec + off + 8);
            c->code[w].c = (int32_t)rd32(sec + off + 12);
            c->code[w].imm = (int32_t)rd32(sec + off + 16);
            off += 20;
            w++;
        }
    }
    return 0;
}

static void archive_save(void) {
    uint8_t sec[512];
    int i;
    if (!hw.has_disk) {
        return;
    }
    mset(sec, 0, 512);
    wr32(sec + 0, 0x4C4F5645u); /* EVOL */
    wr32(sec + 4, 1);
    wr32(sec + 8, (uint32_t)generation);
    wr32(sec + 12, (uint32_t)incumbent.id);
    wr32(sec + 16, (uint32_t)incumbent.parent);
    wr32(sec + 20, (uint32_t)incumbent.fit);
    wr32(sec + 24, (uint32_t)swaps);
    wr32(sec + 28, (uint32_t)rollbacks);
    wr32(sec + 32, (uint32_t)evals);
    wr32(sec + 36, (uint32_t)next_id);
    wr32(sec + 40, (uint32_t)lineage_n);
    for (i = 0; i < 8; i++) {
        wr32(sec + 44 + i * 4, (uint32_t)lineage[i]);
    }
    if (evoos_hw_ata_lba(META_LBA0, sec, 1) != 0) {
        return;
    }
    save_code(META_LBA0 + 1, &incumbent);
    save_code(META_LBA0 + 7, &rollback);
}

static int archive_load(void) {
    uint8_t sec[512];
    int i;
    if (!hw.has_disk) {
        return 0;
    }
    mset(sec, 0, 512);
    if (evoos_hw_ata_lba(META_LBA0, sec, 0) != 0) {
        return 0;
    }
    if (rd32(sec + 0) != 0x4C4F5645u || rd32(sec + 4) != 1) {
        return 0;
    }
    if (load_code(META_LBA0 + 1, &incumbent) != 0 || incumbent.n <= 0) {
        return 0;
    }
    load_code(META_LBA0 + 7, &rollback);
    generation = (int32_t)rd32(sec + 8);
    swaps = (int32_t)rd32(sec + 24);
    rollbacks = (int32_t)rd32(sec + 28);
    evals = (int32_t)rd32(sec + 32);
    next_id = (int32_t)rd32(sec + 36);
    if (next_id < 3) {
        next_id = 3;
    }
    lineage_n = (int32_t)rd32(sec + 40);
    if (lineage_n > 8) {
        lineage_n = 8;
    }
    for (i = 0; i < 8; i++) {
        lineage[i] = (int32_t)rd32(sec + 44 + i * 4);
    }
    pop[0] = incumbent;
    resumed = 1;
    live_pc = 0;
    mset(live_r, 0, sizeof(live_r));
    return 1;
}

static void lineage_push(int32_t id) {
    if (lineage_n < 8) {
        lineage[lineage_n++] = id;
    } else {
        int i;
        for (i = 0; i < 7; i++) {
            lineage[i] = lineage[i + 1];
        }
        lineage[7] = id;
    }
}

static void do_swap(const MetaCand *c, const char *why) {
    rollback = incumbent;
    incumbent = *c;
    live_pc = 0;
    mset(live_r, 0, sizeof(live_r));
    swaps++;
    swapped_live = 1;
    iso_at_swap = hw.isolation_n;
    last_shadow = c->fit;
    lineage_push(c->id);
    pop[0] = incumbent;
    log_s("swap id=");
    log_i(c->id);
    log_s(" from=");
    log_i(c->parent);
    log_s(" fit=");
    log_i(c->fit);
    log_s(" via=");
    log_s(c->origin);
    log_s(" ");
    log_s(why);
    log_s("\n");
    archive_save();
}

static void do_rollback(const char *why) {
    if (rollback.n <= 0) {
        return;
    }
    incumbent = rollback;
    live_pc = 0;
    mset(live_r, 0, sizeof(live_r));
    rollbacks++;
    swapped_live = 0;
    log_s("rollback id=");
    log_i(incumbent.id);
    log_s(" ");
    log_s(why);
    log_s("\n");
    archive_save();
}

/* ---- live interpreter (hw trampolines; guest state is hw.ctx) ---- */

static uint32_t rget(int rd) { return (rd <= 0 || rd > 7) ? 0u : live_r[rd]; }

static void rset(int rd, uint32_t v) {
    if (rd > 0 && rd < 8) {
        live_r[rd] = v;
    }
}

int evoos_meta_live_burst(int n_ops) {
    int k;
    int n = incumbent.n;
    if (n <= 0) {
        return 0;
    }
    for (k = 0; k < n_ops; k++) {
        EvoOSInstr ins;
        int32_t op, a, b, c, imm, tgt;
        uint32_t ra, rb, rc;
        if (live_pc < 0 || live_pc >= n) {
            return 0;
        }
        ins = incumbent.code[live_pc];
        op = ins.op;
        a = ins.a;
        b = ins.b;
        c = ins.c;
        imm = ins.imm;
        ra = rget(a);
        rb = rget(b);
        rc = rget(c);
        tgt = live_pc + 1 + imm;
        switch (op) {
        case EVOOS_HALT:
            return 0;
        case EVOOS_NOP:
            live_pc++;
            break;
        case EVOOS_MOV:
            rset(a, rb);
            live_pc++;
            break;
        case EVOOS_MOVI:
            rset(a, (uint32_t)imm);
            live_pc++;
            break;
        case EVOOS_ADD:
            rset(a, rb + rc);
            live_pc++;
            break;
        case EVOOS_SUB:
            rset(a, rb - rc);
            live_pc++;
            break;
        case EVOOS_MUL:
            rset(a, rb * rc);
            live_pc++;
            break;
        case EVOOS_AND:
            rset(a, rb & rc);
            live_pc++;
            break;
        case EVOOS_OR:
            rset(a, rb | rc);
            live_pc++;
            break;
        case EVOOS_XOR:
            rset(a, rb ^ rc);
            live_pc++;
            break;
        case EVOOS_SHL:
            rset(a, rb << (rc & 31u));
            live_pc++;
            break;
        case EVOOS_SHR:
            rset(a, rb >> (rc & 31u));
            live_pc++;
            break;
        case EVOOS_NOT:
            rset(a, ~rb);
            live_pc++;
            break;
        case EVOOS_ADDI:
            rset(a, rb + (uint32_t)imm);
            live_pc++;
            break;
        case EVOOS_LOAD:
            rset(a, (uint32_t)evoos_hw_load((int32_t)(rb + (uint32_t)imm)));
            live_pc++;
            break;
        case EVOOS_STORE:
            evoos_hw_store((int32_t)(ra + (uint32_t)imm), (int32_t)rb);
            live_pc++;
            break;
        case EVOOS_CMP: {
            int32_t cmp = ((int32_t)rb < (int32_t)rc) ? -1 : ((int32_t)rb > (int32_t)rc) ? 1 : 0;
            rset(a, (uint32_t)cmp);
            live_pc++;
            break;
        }
        case EVOOS_BEQ:
            live_pc = (ra == rb && tgt >= 0 && tgt < n) ? tgt : live_pc + 1;
            break;
        case EVOOS_BNE:
            live_pc = (ra != rb && tgt >= 0 && tgt < n) ? tgt : live_pc + 1;
            break;
        case EVOOS_BLT:
            live_pc = ((int32_t)ra < (int32_t)rb && tgt >= 0 && tgt < n) ? tgt : live_pc + 1;
            break;
        case EVOOS_BGE:
            live_pc = ((int32_t)ra >= (int32_t)rb && tgt >= 0 && tgt < n) ? tgt : live_pc + 1;
            break;
        case EVOOS_JMP:
            if (tgt >= 0 && tgt < n) {
                live_pc = tgt;
            } else {
                return 0;
            }
            break;
        case EVOOS_JAL:
            rset(a, (uint32_t)(live_pc + 1));
            if (tgt >= 0 && tgt < n) {
                live_pc = tgt;
            } else {
                return 0;
            }
            break;
        case EVOOS_JR:
            if ((int32_t)ra >= 0 && (int32_t)ra < n) {
                live_pc = (int32_t)ra;
            } else {
                return 0;
            }
            break;
        case EVOOS_RUNCTX:
            evoos_hw_runctx((int32_t)ra, (int32_t)rb);
            live_pc++;
            break;
        case EVOOS_RUNCTXI:
            evoos_hw_runctx((int32_t)ra, imm);
            live_pc++;
            break;
        case EVOOS_CTXGET:
            rset(a, (uint32_t)evoos_hw_ctxget((int32_t)rb, imm));
            live_pc++;
            break;
        case EVOOS_CTXSET:
            evoos_hw_ctxset((int32_t)ra, imm, (int32_t)rb);
            live_pc++;
            break;
        case EVOOS_ADDPROT:
            evoos_hw_addprot((int32_t)ra, (int32_t)rb, (int32_t)rc);
            live_pc++;
            break;
        case EVOOS_CLRPROT:
            evoos_hw_clrprot((int32_t)ra);
            live_pc++;
            break;
        case EVOOS_RDTIME:
            rset(a, (uint32_t)hw.cycles);
            live_pc++;
            break;
        case EVOOS_RDFAULT:
            rset(a, 0);
            live_pc++;
            break;
        case EVOOS_BLKRD:
            rset(a, (uint32_t)evoos_hw_blkrd((int32_t)rb, (int32_t)rc));
            live_pc++;
            break;
        case EVOOS_BLKWR:
            evoos_hw_blkwr((int32_t)ra, (int32_t)rb);
            live_pc++;
            break;
        case EVOOS_SVC:
            evoos_hw_svc((int32_t)ra);
            live_pc++;
            break;
        default:
            return 0;
        }
    }
    return 1;
}

void evoos_meta_record_spawn(int slot, const char *kind) {
    MetaTrace *t = &traces[tr_i];
    t->kind = 1;
    t->slot = slot;
    t->aux = kind_id(kind);
    tr_i = (tr_i + 1) % META_TRACE;
    if (n_trace < META_TRACE) {
        n_trace++;
    }
}

void evoos_meta_record_svc(int32_t code, int32_t ctx) {
    MetaTrace *t = &traces[tr_i];
    t->kind = 2;
    t->slot = ctx > 0 ? ctx - 1 : 0;
    t->aux = code;
    tr_i = (tr_i + 1) % META_TRACE;
    if (n_trace < META_TRACE) {
        n_trace++;
    }
}

void evoos_meta_on_live_metrics(int32_t isolation_n) {
    int i, progressed = 0, n_g = 0;
    if (isolation_n > 0 && swapped_live) {
        do_rollback("live isolation");
        return;
    }
    for (i = 1; i < hw.n_contexts; i++) {
        if (hw.ctx[i].name[0] == 0) {
            continue;
        }
        n_g++;
        if (hw.ctx[i].cycles > 0 || hw.ctx[i].halted || hw.ctx[i].fault) {
            progressed++;
        }
    }
    if (n_g > 0) {
        live_fit = (1000 * progressed) / n_g;
        if (isolation_n > 0) {
            live_fit = 0;
        }
    }
}

void evoos_meta_tick(void) {
    MetaCand child;
    const MetaCand *parent;
    const MetaCand *other;
    int safe = 0;
    int32_t fit;
    if (!evo_on) {
        return;
    }
    tick_div++;
    if ((tick_div & 15) != 0) {
        return;
    }
    if (n_trace < 3) {
        return;
    }
    generation++;
    {
        int inc_safe = 0;
        incumbent.fit = shadow_eval(&incumbent, &inc_safe);
        if (!inc_safe) {
            incumbent.fit = 0;
        }
        pop[0].fit = incumbent.fit;
        {
            int psafe = 0;
            int32_t pf = shadow_eval(&pop[1], &psafe);
            if (psafe) {
                pop[1].fit = pf;
            }
            if (psafe && inc_safe && incumbent.fit > 0 && pf >= incumbent.fit + 200 &&
                pop[1].id != incumbent.id) {
                do_swap(&pop[1], "challenger");
                log_s("evo gen=");
                log_i(generation);
                log_s(" evals=");
                log_i(evals);
                log_s(" challenger fit=");
                log_i(pf);
                log_s("\n");
                evals++;
                return;
            }
        }
    }
    parent = &pop[rndn(META_POP)];
    if (parent->n <= 0) {
        parent = &incumbent;
    }
    other = &pop[rndn(META_POP)];
    mutate(&child, parent, other->n ? other : &incumbent);
    fit = shadow_eval(&child, &safe);
    child.fit = fit;
    evals++;
    last_shadow = fit;
    if (safe && fit > pop[META_POP - 1].fit) {
        pop[META_POP - 1] = child;
        {
            int i;
            for (i = META_POP - 1; i > 1; i--) {
                if (pop[i].fit > pop[i - 1].fit) {
                    MetaCand t = pop[i];
                    pop[i] = pop[i - 1];
                    pop[i - 1] = t;
                }
            }
        }
    }
    if (safe && incumbent.fit > 0 && fit >= incumbent.fit + META_MARGIN_MILLI) {
        do_swap(&child, "shadow");
    }
    if ((generation % 8) == 0) {
        log_s("evo gen=");
        log_i(generation);
        log_s(" evals=");
        log_i(evals);
        log_s(" cand=");
        log_i(child.id);
        log_s(" fit=");
        log_i(fit);
        log_s(" inc=");
        log_i(incumbent.fit);
        log_s(" origin=");
        log_s(child.origin);
        log_s("\n");
        if (!(safe && fit >= incumbent.fit + META_MARGIN_MILLI)) {
            log_s("evo noswap gen=");
            log_i(generation);
            log_s("\n");
        }
        archive_save();
    }
}

void evoos_meta_toggle(void) {
    evo_on = !evo_on;
    log_s(evo_on ? "evo on\n" : "evo off\n");
}

void evoos_meta_show_lineage(void) {
    int i;
    log_s("lineage n=");
    log_i(lineage_n);
    log_s(" inc=");
    log_i(incumbent.id);
    log_s(" parent=");
    log_i(incumbent.parent);
    log_s(" origin=");
    log_s(incumbent.origin);
    log_s(" ids=");
    for (i = 0; i < lineage_n; i++) {
        log_i(lineage[i]);
        log_s(i + 1 < lineage_n ? "," : "");
    }
    log_s("\n");
}

EvoOSMetaStats evoos_meta_stats(void) {
    EvoOSMetaStats s;
    int i;
    mset(&s, 0, sizeof(s));
    s.generation = generation;
    s.incumbent_id = incumbent.id;
    s.parent_id = incumbent.parent;
    s.live_fit = live_fit;
    s.shadow_fit = last_shadow;
    s.evals = evals;
    s.swaps = swaps;
    s.rollbacks = rollbacks;
    s.evo_on = evo_on;
    s.n_code = incumbent.n;
    s.resumed = resumed;
    s.lineage_n = lineage_n;
    for (i = 0; i < 8; i++) {
        s.lineage[i] = lineage[i];
    }
    return s;
}

int evoos_meta_using_interp(void) { return 1; }

void evoos_meta_init(void) {
    int i;
    rng_s = 0xA341316Cu;
    (void)seed_s99_code;
    cand_from(&incumbent, seed_incumbent_code, SEED_INCUMBENT_N, 1, 0, seed_incumbent_origin);
    cand_from(&rollback, seed_incumbent_code, SEED_INCUMBENT_N, 1, 0, seed_incumbent_origin);
    cand_from(&pop[0], seed_incumbent_code, SEED_INCUMBENT_N, 1, 0, seed_incumbent_origin);
    cand_from(&pop[1], seed_persist_rr_code, SEED_PERSIST_RR_N, 2, 0, seed_persist_rr_origin);
    for (i = 2; i < META_POP; i++) {
        mutate(&pop[i], &incumbent, &pop[1]);
    }
    lineage_n = 0;
    lineage_push(1);
    live_pc = 0;
    mset(live_r, 0, sizeof(live_r));
    if (archive_load()) {
        log_s("archive loaded gen=");
        log_i(generation);
        log_s(" inc=");
        log_i(incumbent.id);
        log_s(" lineage resume origin=");
        log_s(incumbent.origin);
        log_s("\n");
    } else {
        log_s("archive empty; seed s99 id=1 challenger persist_rr id=2\n");
        archive_save();
    }
    log_s("meta online evo=");
    log_s(evo_on ? "on" : "off");
    log_s(" incumbent=");
    log_s(incumbent.origin);
    log_s(" n=");
    log_i(incumbent.n);
    log_s("\n");
}
