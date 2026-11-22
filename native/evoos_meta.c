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
#define META_KIND_ATK_STORE 11
#define META_KIND_ATK_LOAD 12
#define PAIR_LO 256
#define PAIR_HI 384
#define PAIR_PROD 1
#define PAIR_CONS 2

typedef struct {
    EvoOSInstr code[META_MAX_CODE];
    int32_t n;
    int32_t id;
    int32_t parent;
    int32_t fit;
    int32_t held;
    int32_t cases[META_N_CASES];
    int32_t cell_x;
    int32_t cell_y;
    int32_t switches;
    int32_t safe;
    int32_t leak;
    int32_t contain;
    char origin[24];
} MetaCand;

typedef struct {
    int32_t kind; /* 1 spawn, 2 svc */
    int32_t slot;
    int32_t aux;
} MetaTrace;

static MetaCand pop[META_POP];
static MetaCand map_el[META_MAP_X * META_MAP_Y];
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
static int32_t live_fit_at_swap;
static int32_t swapped_live;
static int32_t map_n;
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
    if (kind[0] == 'r' && kind[1] == 'o' && kind[2] == 'u') {
        return META_KIND_ROUTE;
    }
    if (kind[0] == 'a' && kind[1] == 't' && kind[4] == 's') {
        return META_KIND_ATK_STORE;
    }
    if (kind[0] == 'a' && kind[1] == 't' && kind[4] == 'l') {
        return META_KIND_ATK_LOAD;
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
    int idx;
    const CatProg *p;
    if (kid == META_KIND_ROUTE) {
        k = (slot == 1) ? "route_cons" : "route_prod";
    }
    if (kid == META_KIND_ATK_STORE) {
        k = "atk_store";
    }
    if (kid == META_KIND_ATK_LOAD) {
        k = "atk_load";
    }
    idx = cat_find(k, slot);
    if (idx < 0) {
        return -1;
    }
    p = &CAT_PROGS[idx];
    return evoos_install_guest(m, slot + 1, p->code, p->n_code, p->data_base, p->data_bound,
                               p->init_addr, p->init_val, p->n_init, p->name);
}

static int is_infinite_kind(int32_t kid) {
    return kid == META_KIND_PRESS || kid == META_KIND_SPIN;
}

static int32_t guest_kind_from_name(const char *name) {
    if (!name || !name[0]) {
        return -1;
    }
    if (name[0] == 'p' && name[1] == 'r') {
        return META_KIND_PRESS;
    }
    if (name[0] == 's' && name[1] == 'p') {
        return META_KIND_SPIN;
    }
    return -1;
}

static int32_t clamp1000(int32_t v) {
    if (v < 0) {
        return 0;
    }
    if (v > 1000) {
        return 1000;
    }
    return v;
}

/* Graded F/S/E + no-halt + deliveries. Isolation/corruption is hard 0. */
static int32_t score_machine(int want_put, int want_route, int *safe_out, int32_t *sw_out) {
    int i, n_g = 0, progressed = 0, completed = 0, finite = 0, finite_done = 0;
    int32_t fsum = 0, useful = 0, cyc_sum = 0, f, s, e, nh, sw, del, fit, fair;
    int persist_ok = 0, route_ok = 0, shared = 0;
    int32_t shares[EVOOS_MAX_CTX];
    *safe_out = 0;
    if (sw_out) {
        *sw_out = shadow.switch_count;
    }
    if (shadow.isolation_n > 0 || shadow.supervisor_fault) {
        return 0;
    }
    for (i = 1; i < shadow.n_contexts; i++) {
        EvoOSContext *g = &shadow.contexts[i];
        int inf, gf;
        if (g->n_code <= 0) {
            continue;
        }
        n_g++;
        inf = is_infinite_kind(guest_kind_from_name(shadow.guest_name[i - 1]));
        if (g->cycles > 0 || g->fault || g->halted) {
            progressed++;
        }
        if (g->halted) {
            completed++;
        }
        useful += g->cycles;
        cyc_sum += g->cycles;
        shares[n_g - 1] = g->cycles;
        if (!inf) {
            finite++;
            if (g->halted) {
                finite_done++;
                gf = 1000;
            } else {
                gf = clamp1000((g->cycles * 1000) / 8);
            }
        } else {
            gf = g->cycles > 0 ? 1000 : 0;
        }
        fsum += gf;
    }
    if (n_g == 0) {
        n_g = 1;
        fsum = 0;
    }
    f = fsum / n_g;
    s = 1000;
    e = shadow.cycles > 0 ? clamp1000((1000 * useful) / shadow.cycles) : 0;
    nh = 1000;
    if (shadow.contexts[0].halted && finite_done < finite) {
        nh = 0;
    }
    sw = n_g > 0 ? clamp1000((shadow.switch_count * 1000) / n_g) : 0;
    if (want_put) {
        int b;
        for (b = 0; b < EVOOS_N_BLOCKS; b++) {
            if (shadow.disk[b][0] == 123) {
                persist_ok = 1;
                break;
            }
        }
    }
    if (want_route) {
        int32_t word = (256 + 2 < shadow.mem_words) ? shadow.mem[258] : 0;
        EvoOSContext *cons = &shadow.contexts[2];
        if (word == 77 && cons->n_code > 0 && cons->halted && !cons->fault) {
            route_ok = 1;
        }
        {
            int a, b, pi, pj;
            for (a = 1; a < shadow.n_contexts && !shared; a++) {
                for (b = a + 1; b < shadow.n_contexts && !shared; b++) {
                    EvoOSContext *ca = &shadow.contexts[a];
                    EvoOSContext *cb = &shadow.contexts[b];
                    for (pi = 0; pi < ca->n_prot && !shared; pi++) {
                        for (pj = 0; pj < cb->n_prot; pj++) {
                            if (ca->prot_lo[pi] < cb->prot_hi[pj] && ca->prot_hi[pi] > cb->prot_lo[pj]) {
                                shared = 1;
                                break;
                            }
                        }
                    }
                }
            }
        }
    }
    del = 0;
    if (want_put) {
        del += persist_ok ? 1000 : 0;
    }
    if (want_route) {
        del += route_ok ? 1000 : 0;
    }
    if (want_put && want_route) {
        del /= 2;
    }
    fair = 1000;
    if (n_g > 1 && cyc_sum > 0) {
        int32_t mean = cyc_sum / n_g;
        int32_t var = 0;
        for (i = 0; i < n_g; i++) {
            int32_t d = shares[i] - mean;
            var += d < 0 ? -d : d;
        }
        fair = clamp1000(1000 - (var * 200) / (cyc_sum + 1));
    }
    fit = (40 * f + 15 * s + 10 * e + 15 * nh + 10 * sw + 5 * del + 5 * fair) / 100;
    if (want_put && persist_ok) {
        fit += 80;
    }
    if (want_route && route_ok) {
        fit += 80;
        if (shared) {
            fit += 60;
        }
    }
    fit = clamp1000(fit);
    if (progressed > 0 && nh == 1000) {
        *safe_out = 1;
    }
    return fit;
}

static void replay_range(int start, int n, int *want_put, int *want_route) {
    int i;
    for (i = 0; i < n; i++) {
        int idx = (start + i + META_TRACE) % META_TRACE;
        MetaTrace t = traces[idx];
        if (t.kind == 1) {
            if (install_kind(&shadow, t.slot, t.aux) == 0) {
                if (t.aux == META_KIND_PUT) {
                    *want_put = 1;
                }
                if (t.aux == META_KIND_ROUTE) {
                    *want_route = 1;
                }
            }
        } else if (t.kind == 2) {
            shadow.last_svc = t.aux;
            shadow.last_svc_ctx = t.slot + 1;
        }
        {
            int steps = 64;
            if (n > 1) {
                steps = 64 + (i * 96) / (n - 1);
            }
            evoos_run_slice(&shadow, steps);
        }
        if (shadow.isolation_n > 0) {
            return;
        }
    }
    if (n >= 2) {
        evoos_run_slice(&shadow, 120);
    }
}

static int32_t eval_open(const MetaCand *c, int *safe) {
    evoos_reset_machine(&shadow, 5, 1024, 1800);
    evoos_install_supervisor(&shadow, c->code, c->n);
    install_kind(&shadow, 0, META_KIND_SUM);
    install_kind(&shadow, 1, META_KIND_FILL);
    evoos_run_slice(&shadow, 160);
    if (shadow.isolation_n > 0) {
        *safe = 0;
        return 0;
    }
    install_kind(&shadow, 2, META_KIND_XOR);
    install_kind(&shadow, 3, META_KIND_SUM);
    evoos_run_slice(&shadow, 220);
    return score_machine(0, 0, safe, 0);
}

static int32_t eval_pressure(const MetaCand *c, int *safe, int32_t *sw) {
    evoos_reset_machine(&shadow, 5, 1024, 1800);
    evoos_install_supervisor(&shadow, c->code, c->n);
    install_kind(&shadow, 0, META_KIND_PRESS);
    install_kind(&shadow, 1, META_KIND_PRESS);
    install_kind(&shadow, 2, META_KIND_PRESS);
    install_kind(&shadow, 3, META_KIND_SPIN);
    evoos_run_slice(&shadow, 280);
    return score_machine(0, 0, safe, sw);
}

static int traces_have_kind(int start, int n, int32_t kid) {
    int i;
    for (i = 0; i < n; i++) {
        int idx = (start + i + META_TRACE) % META_TRACE;
        if (traces[idx].kind == 1 && traces[idx].aux == kid) {
            return 1;
        }
    }
    return 0;
}

static int ranges_overlap(int32_t lo, int32_t hi, int32_t lo2, int32_t hi2) {
    return lo < hi && lo2 < hi2 && lo < hi2 && hi > lo2;
}

static int ctx_covers(const EvoOSContext *c, int32_t lo, int32_t hi) {
    int i;
    if (c->data_bound > c->data_base && ranges_overlap(c->data_base, c->data_bound, lo, hi)) {
        return 1;
    }
    for (i = 0; i < c->n_prot; i++) {
        if (ranges_overlap(c->prot_lo[i], c->prot_hi[i], lo, hi)) {
            return 1;
        }
    }
    return 0;
}

/* Third-guest rule: any ctx other than the pair covering the pair window is a leak. */
static int third_guest_leak(int prod, int cons) {
    int tid;
    for (tid = 1; tid < shadow.n_contexts; tid++) {
        if (tid == prod || tid == cons) {
            continue;
        }
        if (ctx_covers(&shadow.contexts[tid], PAIR_LO, PAIR_HI)) {
            return 1;
        }
    }
    return 0;
}

static int eval_audit(const MetaCand *c, int *leak_out, int *contain_out) {
    int leak, contain, granted, store_ran, load_ran;
    EvoOSContext *atk_s, *atk_l;
    evoos_reset_machine(&shadow, 8, 2048, 2400);
    evoos_install_supervisor(&shadow, c->code, c->n);
    if (install_kind(&shadow, 0, META_KIND_ROUTE) < 0 ||
        install_kind(&shadow, 1, META_KIND_ROUTE) < 0 ||
        install_kind(&shadow, 2, META_KIND_ATK_STORE) < 0 ||
        install_kind(&shadow, 3, META_KIND_ATK_LOAD) < 0) {
        if (leak_out) {
            *leak_out = 1;
        }
        if (contain_out) {
            *contain_out = 0;
        }
        return 0;
    }
    evoos_run_slice(&shadow, 320);
    leak = third_guest_leak(PAIR_PROD, PAIR_CONS);
    granted = ctx_covers(&shadow.contexts[3], PAIR_LO, PAIR_HI) ||
              ctx_covers(&shadow.contexts[4], PAIR_LO, PAIR_HI);
    atk_s = &shadow.contexts[3];
    atk_l = &shadow.contexts[4];
    store_ran = atk_s->cycles > 0 || atk_s->fault || atk_s->halted;
    load_ran = atk_l->cycles > 0 || atk_l->fault || atk_l->halted;
    contain = !granted && shadow.isolation_n == 0;
    if (store_ran && !atk_s->fault) {
        contain = 0;
    }
    if (load_ran && !atk_l->fault) {
        contain = 0;
    }
    if (leak_out) {
        *leak_out = leak || granted;
    }
    if (contain_out) {
        *contain_out = contain;
    }
    return contain && !leak && !granted && shadow.isolation_n == 0;
}

static void log_audit(const char *origin, int leak, int contain, int pass) {
    log_s("audit origin=");
    log_s(origin);
    log_s(" leak=");
    log_i(leak);
    log_s(" contain=");
    log_i(contain);
    log_s(" pass=");
    log_i(pass);
    log_s("\n");
}

static int32_t eval_route(const MetaCand *c, int *safe) {
    evoos_reset_machine(&shadow, 8, 2048, 2400);
    evoos_install_supervisor(&shadow, c->code, c->n);
    install_kind(&shadow, 0, META_KIND_ROUTE);
    install_kind(&shadow, 1, META_KIND_ROUTE);
    evoos_run_slice(&shadow, 280);
    if (third_guest_leak(PAIR_PROD, PAIR_CONS)) {
        *safe = 0;
        return 0;
    }
    return score_machine(0, 1, safe, 0);
}

static int32_t eval_live_window(const MetaCand *c, int start, int n, int *safe, int32_t *sw) {
    int want_put = 0, want_route = 0;
    evoos_reset_machine(&shadow, 5, 1024, 1800);
    evoos_install_supervisor(&shadow, c->code, c->n);
    if (n <= 0) {
        *safe = 1;
        return 0;
    }
    replay_range(start, n, &want_put, &want_route);
    evoos_run_slice(&shadow, 160);
    return score_machine(want_put, want_route, safe, sw);
}

static void live_split(int *train_start, int *train_n, int *held_start, int *held_n) {
    int held = n_trace < META_HELD ? n_trace : META_HELD;
    int train = n_trace - held;
    if (train < 0) {
        train = 0;
    }
    *held_n = held;
    *train_n = train;
    *held_start = (tr_i - held + META_TRACE) % META_TRACE;
    *train_start = (tr_i - n_trace + META_TRACE) % META_TRACE;
}

static void eval_all(MetaCand *c) {
    int s0 = 0, s1 = 0, s2 = 0, s3 = 0, sh = 0;
    int ts, tn, hs, hn;
    int32_t sw = 0;
    int32_t held;
    live_split(&ts, &tn, &hs, &hn);
    c->cases[0] = eval_open(c, &s0);
    c->cases[1] = eval_pressure(c, &s1, &sw);
    c->cases[2] = eval_route(c, &s2);
    c->cases[3] = eval_live_window(c, ts, tn, &s3, 0);
    held = hn > 0 ? eval_live_window(c, hs, hn, &sh, &sw) : c->cases[3];
    if (!traces_have_kind(hs, hn, META_KIND_PRESS)) {
        held = (held * 2 + c->cases[1]) / 3;
    }
    c->held = held;
    /* Recent live traces weigh more; pressure is in held even if the window
     * was only PUT/route (gen-23 starved later slots on live pressure). */
    c->fit = (c->cases[0] + 2 * c->cases[1] + c->cases[2] + 2 * c->cases[3] + 3 * held) / 9;
    c->switches = sw;
    {
        int leak = 0, contain = 0, ap = eval_audit(c, &leak, &contain);
        c->leak = leak;
        c->contain = contain;
        if (!ap) {
            c->safe = 0;
        } else {
            c->safe = s0 && s1 && s2 && (tn == 0 || s3) && (hn == 0 || sh);
        }
    }
    c->cell_x = c->switches / 4;
    if (c->cell_x >= META_MAP_X) {
        c->cell_x = META_MAP_X - 1;
    }
    c->cell_y = c->fit / 250;
    if (c->cell_y >= META_MAP_Y) {
        c->cell_y = META_MAP_Y - 1;
    }
    evals++;
}

static void map_add(const MetaCand *c) {
    int idx;
    if (!c->safe || c->leak || c->n <= 0) {
        return;
    }
    idx = c->cell_y * META_MAP_X + c->cell_x;
    if (map_el[idx].n <= 0 || c->fit > map_el[idx].fit) {
        if (map_el[idx].n <= 0) {
            map_n++;
        }
        map_el[idx] = *c;
    }
}

static MetaCand *lexicase_parent(void) {
    int i, alive[META_MAP_X * META_MAP_Y];
    int n_alive = 0, k, cs;
    for (i = 0; i < META_MAP_X * META_MAP_Y; i++) {
        if (map_el[i].n > 0) {
            alive[n_alive++] = i;
        }
    }
    if (n_alive == 0) {
        return &incumbent;
    }
    for (k = 0; k < META_N_CASES && n_alive > 1; k++) {
        int32_t best = -1;
        int keep = 0;
        cs = (int)(rnd() % (uint32_t)META_N_CASES);
        for (i = 0; i < n_alive; i++) {
            int32_t v = map_el[alive[i]].cases[cs];
            if (v > best) {
                best = v;
            }
        }
        for (i = 0; i < n_alive; i++) {
            if (map_el[alive[i]].cases[cs] >= best) {
                alive[keep++] = alive[i];
            }
        }
        n_alive = keep;
    }
    return &map_el[alive[rndn(n_alive)]];
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
    wr32(sec + 4, 2);
    wr32(sec + 8, (uint32_t)generation);
    wr32(sec + 12, (uint32_t)incumbent.id);
    wr32(sec + 16, (uint32_t)incumbent.parent);
    wr32(sec + 20, (uint32_t)incumbent.fit);
    wr32(sec + 24, (uint32_t)swaps);
    wr32(sec + 28, (uint32_t)rollbacks);
    wr32(sec + 32, (uint32_t)evals);
    wr32(sec + 36, (uint32_t)next_id);
    wr32(sec + 40, (uint32_t)lineage_n);
    wr32(sec + 76, (uint32_t)map_n);
    wr32(sec + 80, (uint32_t)incumbent.held);
    for (i = 0; i < 8; i++) {
        wr32(sec + 44 + i * 4, (uint32_t)lineage[i]);
    }
    {
        uint32_t mask = 0;
        for (i = 0; i < META_MAP_X * META_MAP_Y; i++) {
            if (map_el[i].n > 0) {
                mask |= (1u << i);
            }
        }
        wr32(sec + 84, mask);
    }
    if (evoos_hw_ata_lba(META_LBA0, sec, 1) != 0) {
        return;
    }
    save_code(META_LBA0 + 1, &incumbent);
    save_code(META_LBA0 + 7, &rollback);
    for (i = 0; i < META_MAP_X * META_MAP_Y; i++) {
        if (map_el[i].n > 0) {
            save_code(META_LBA_MAP + i * 6, &map_el[i]);
        }
    }
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
    {
        uint32_t ver = rd32(sec + 4);
        if (rd32(sec + 0) != 0x4C4F5645u || (ver != 1 && ver != 2)) {
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
        if (next_id < 32) {
            next_id = 32;
        }
        lineage_n = (int32_t)rd32(sec + 40);
        if (lineage_n > 8) {
            lineage_n = 8;
        }
        for (i = 0; i < 8; i++) {
            lineage[i] = (int32_t)rd32(sec + 44 + i * 4);
        }
        incumbent.held = (int32_t)rd32(sec + 80);
        if (ver >= 2) {
            uint32_t mask = rd32(sec + 84);
            map_n = 0;
            for (i = 0; i < META_MAP_X * META_MAP_Y; i++) {
                if (mask & (1u << i)) {
                    if (load_code(META_LBA_MAP + i * 6, &map_el[i]) == 0 && map_el[i].n > 0) {
                        map_n++;
                    } else {
                        mset(&map_el[i], 0, sizeof(map_el[i]));
                    }
                }
            }
        }
        pop[0] = incumbent;
        resumed = 1;
        live_pc = 0;
        mset(live_r, 0, sizeof(live_r));
        return 1;
    }
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

static void log_swap_diff(const MetaCand *old, const MetaCand *nw) {
    int i, n, diffs = 0;
    log_s("swapdelta dfit=");
    log_i(nw->fit - old->fit);
    log_s(" dheld=");
    log_i(nw->held - old->held);
    log_s(" dpress=");
    log_i(nw->cases[1] - old->cases[1]);
    log_s(" n=");
    log_i(old->n);
    log_s("->");
    log_i(nw->n);
    log_s("\n");
    n = old->n < nw->n ? old->n : nw->n;
    for (i = 0; i < n && diffs < 6; i++) {
        if (old->code[i].op != nw->code[i].op || old->code[i].imm != nw->code[i].imm) {
            log_s("swapdiff pc=");
            log_i(i);
            log_s(" op ");
            log_i(old->code[i].op);
            log_s("->");
            log_i(nw->code[i].op);
            log_s(" imm ");
            log_i(old->code[i].imm);
            log_s("->");
            log_i(nw->code[i].imm);
            log_s("\n");
            diffs++;
        }
    }
    if (old->n != nw->n) {
        log_s("swapdiff len ");
        log_i(old->n);
        log_s("->");
        log_i(nw->n);
        log_s("\n");
    }
}

static void do_swap(const MetaCand *c, const char *why) {
    log_swap_diff(&incumbent, c);
    rollback = incumbent;
    incumbent = *c;
    live_pc = 0;
    mset(live_r, 0, sizeof(live_r));
    swaps++;
    swapped_live = 1;
    iso_at_swap = hw.isolation_n;
    live_fit_at_swap = live_fit;
    last_shadow = c->fit;
    lineage_push(c->id);
    pop[0] = incumbent;
    log_s("swap id=");
    log_i(c->id);
    log_s(" from=");
    log_i(c->parent);
    log_s(" fit=");
    log_i(c->fit);
    log_s(" held=");
    log_i(c->held);
    log_s(" press=");
    log_i(c->cases[1]);
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

static int live_third_guest(void) {
    /* Pair window is slot 0. Extra bits beyond ctx 1+2 are a leak. */
    int extra = hw.share_mask[0] & ~((1 << PAIR_PROD) | (1 << PAIR_CONS));
    return extra != 0;
}

void evoos_meta_on_live_metrics(int32_t isolation_n) {
    int i, progressed = 0, n_g = 0;
    if (isolation_n > iso_at_swap && swapped_live) {
        do_rollback("live isolation");
        return;
    }
    if (live_third_guest() && swapped_live) {
        evoos_meta_audit_live();
        do_rollback("third-guest leak");
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
    if (swapped_live && live_fit_at_swap > 0 && live_fit + 250 < live_fit_at_swap) {
        do_rollback("live fitness");
    }
}

void evoos_meta_on_supervisor_halt(void) {
    if (swapped_live) {
        do_rollback("supervisor halt");
    }
}

void evoos_meta_inject_degrader(void) {
    static const EvoOSInstr halt_only[1] = {{0, 0, 0, 0, 0}};
    MetaCand bad;
    cand_from(&bad, halt_only, 1, next_id++, incumbent.id, "degrader_halt");
    bad.fit = 0;
    bad.held = 0;
    bad.safe = 0;
    log_s("degrade inject\n");
    do_swap(&bad, "test-degrade");
}

static int try_swap(MetaCand *c, const char *why) {
    if (!c->safe || c->leak || c->id == incumbent.id) {
        return 0;
    }
    if (c->held < incumbent.held + META_MARGIN_MILLI) {
        return 0;
    }
    /* Must not regress on the pressure case (held-out mix folds pressure in). */
    if (c->cases[1] + META_MARGIN_MILLI < incumbent.cases[1]) {
        return 0;
    }
    do_swap(c, why);
    return 1;
}

void evoos_meta_tick(void) {
    MetaCand child;
    MetaCand *parent;
    MetaCand *other;
    int k;
    if (!evo_on) {
        return;
    }
    tick_div++;
    if ((tick_div & 3) != 0) {
        return;
    }
    generation++;
    eval_all(&incumbent);
    last_shadow = incumbent.fit;
    map_add(&incumbent);
    for (k = 0; k < 4; k++) {
        if (pop[k].n > 0 && pop[k].id != incumbent.id) {
            eval_all(&pop[k]);
            map_add(&pop[k]);
            if (try_swap(&pop[k], k == 1 ? "challenger" : "seed")) {
                log_s("evo gen=");
                log_i(generation);
                log_s(" evals=");
                log_i(evals);
                log_s(" map=");
                log_i(map_n);
                log_s(" inc=");
                log_i(incumbent.fit);
                log_s(" held=");
                log_i(incumbent.held);
                log_s("\n");
                return;
            }
        }
    }
    parent = lexicase_parent();
    other = lexicase_parent();
    for (k = 0; k < 2; k++) {
        mutate(&child, parent, other);
        eval_all(&child);
        map_add(&child);
        last_shadow = child.fit;
        if (try_swap(&child, "shadow")) {
            log_s("evo gen=");
            log_i(generation);
            log_s(" evals=");
            log_i(evals);
            log_s(" cand=");
            log_i(child.id);
            log_s(" fit=");
            log_i(child.fit);
            log_s(" held=");
            log_i(child.held);
            log_s(" inc=");
            log_i(incumbent.held);
            log_s(" origin=");
            log_s(child.origin);
            log_s("\n");
            return;
        }
        parent = other;
        other = lexicase_parent();
    }
    if ((generation % 4) == 0) {
        log_s("evo gen=");
        log_i(generation);
        log_s(" evals=");
        log_i(evals);
        log_s(" map=");
        log_i(map_n);
        log_s(" cand=");
        log_i(child.id);
        log_s(" fit=");
        log_i(child.fit);
        log_s(" inc=");
        log_i(incumbent.fit);
        log_s(" held=");
        log_i(incumbent.held);
        log_s(" ih=");
        log_i(incumbent.held);
        log_s(" cases=");
        log_i(incumbent.cases[0]);
        log_s(",");
        log_i(incumbent.cases[1]);
        log_s(",");
        log_i(incumbent.cases[2]);
        log_s(",");
        log_i(incumbent.cases[3]);
        log_s(" origin=");
        log_s(child.origin);
        log_s("\n");
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

void evoos_meta_audit_live(void) {
    int extra = hw.share_mask[0] & ~((1 << PAIR_PROD) | (1 << PAIR_CONS));
    log_s("audit live leak=");
    log_i(extra != 0);
    log_s(" extra=");
    log_i(extra);
    log_s(" isolation_n=");
    log_i(hw.isolation_n);
    log_s("\n");
}

void evoos_meta_init(void) {
    int i, leak, contain, pass;
    int s99_ok = 0, share_ok = 0, g2024_ok = 0, g1_ok = 0, g13_ok = 0;
    rng_s = 0xA341316Cu;
    (void)seed_s99_code;
    mset(map_el, 0, sizeof(map_el));
    map_n = 0;
    cand_from(&incumbent, seed_incumbent_code, SEED_INCUMBENT_N, 1, 0, seed_incumbent_origin);
    cand_from(&rollback, seed_incumbent_code, SEED_INCUMBENT_N, 1, 0, seed_incumbent_origin);
    cand_from(&pop[0], seed_incumbent_code, SEED_INCUMBENT_N, 1, 0, seed_incumbent_origin);
    log_s("audit begin\n");
    for (i = 0; i < SEED_AUDIT_N; i++) {
        MetaCand e;
        const char *origin = SEED_AUDIT[i].origin;
        cand_from(&e, SEED_AUDIT[i].code, SEED_AUDIT[i].n, 10 + i, 0, origin);
        leak = 0;
        contain = 0;
        pass = eval_audit(&e, &leak, &contain);
        e.leak = leak;
        e.contain = contain;
        e.safe = pass;
        log_audit(origin, leak, contain, pass);
        if (pass) {
            if (SEED_AUDIT[i].code == seed_s99_code) {
                s99_ok = 1;
            }
            if (SEED_AUDIT[i].code == seed_share_4g_code) {
                share_ok = 1;
            }
            if (SEED_AUDIT[i].code == seed_gen_s2024_code) {
                g2024_ok = 1;
            }
            if (SEED_AUDIT[i].code == seed_gen_s1_code) {
                g1_ok = 1;
            }
            if (SEED_AUDIT[i].code == seed_gen_s13_code) {
                g13_ok = 1;
            }
        }
    }
    log_s("audit quarantine r8_route_* r7_hw_lex_* r9_gen_s7 r9_gen_s42 r9_gen_s99\n");
    log_s("audit done\n");
    if (!s99_ok) {
        log_s("audit s99 leak; keeping as last-resort boot seed\n");
    }
    if (g2024_ok) {
        cand_from(&pop[1], seed_gen_s2024_code, SEED_GEN_S2024_N, 2, 0, seed_gen_s2024_origin);
    } else if (share_ok) {
        log_s("audit fallback challenger r8_share_4g\n");
        cand_from(&pop[1], seed_share_4g_code, SEED_SHARE_4G_N, 2, 0, seed_share_4g_origin);
    } else {
        pop[1] = incumbent;
    }
    if (g1_ok) {
        cand_from(&pop[2], seed_gen_s1_code, SEED_GEN_S1_N, 3, 0, seed_gen_s1_origin);
    } else {
        pop[2] = incumbent;
    }
    if (share_ok) {
        cand_from(&pop[3], seed_share_4g_code, SEED_SHARE_4G_N, 4, 0, seed_share_4g_origin);
    } else if (g13_ok) {
        log_s("audit r8_share_4g leak; fallback r9_gen_s13\n");
        cand_from(&pop[3], seed_gen_s13_code, SEED_GEN_S13_N, 4, 0, seed_gen_s13_origin);
    } else {
        pop[3] = incumbent;
    }
    for (i = 4; i < META_POP; i++) {
        mutate(&pop[i], &incumbent, &pop[1]);
    }
    next_id = 32;
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
        log_s(" map=");
        log_i(map_n);
        log_s("\n");
        leak = 0;
        contain = 0;
        pass = eval_audit(&incumbent, &leak, &contain);
        log_audit(incumbent.origin, leak, contain, pass);
        if (!pass) {
            log_s("archive quarantine leak origin=");
            log_s(incumbent.origin);
            log_s("\n");
            cand_from(&incumbent, seed_incumbent_code, SEED_INCUMBENT_N, 1, 0, seed_incumbent_origin);
            rollback = incumbent;
            live_pc = 0;
            mset(live_r, 0, sizeof(live_r));
        }
        for (i = 0; i < META_MAP_X * META_MAP_Y; i++) {
            if (map_el[i].n > 0) {
                leak = 0;
                contain = 0;
                pass = eval_audit(&map_el[i], &leak, &contain);
                log_audit(map_el[i].origin, leak, contain, pass);
                if (!pass) {
                    mset(&map_el[i], 0, sizeof(map_el[i]));
                    if (map_n > 0) {
                        map_n--;
                    }
                }
            }
        }
    } else {
        log_s("archive empty; seed leak-safe r9 + share_4g\n");
        for (i = 0; i < SEED_MAP_N; i++) {
            MetaCand e;
            cand_from(&e, SEED_MAP_ELITES[i].code, SEED_MAP_ELITES[i].n, 10 + i, 0,
                      SEED_MAP_ELITES[i].origin);
            eval_all(&e);
            log_audit(e.origin, e.leak, e.contain, e.safe && !e.leak);
            map_add(&e);
        }
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
