#include "evoos_vm.h"

evoos_store_hook_fn evoos_store_hook = 0;
evoos_runctx_hook_fn evoos_runctx_hook = 0;

static uint32_t rd_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int32_t rd_i32(const uint8_t *p) { return (int32_t)rd_u32(p); }

static int32_t wrap32_add(int32_t a, int32_t b) { return (int32_t)((uint32_t)a + (uint32_t)b); }
static int32_t wrap32_sub(int32_t a, int32_t b) { return (int32_t)((uint32_t)a - (uint32_t)b); }
static int32_t wrap32_mul(int32_t a, int32_t b) { return (int32_t)((uint32_t)a * (uint32_t)b); }
static int32_t wrap32_not(int32_t a) { return (int32_t)(~(uint32_t)a); }

static void e_memset(void *s, int c, size_t n) {
    uint8_t *p = (uint8_t *)s;
    while (n--) {
        *p++ = (uint8_t)c;
    }
}

static int privileged_op(int32_t op) {
    return op == EVOOS_RUNCTX || op == EVOOS_RUNCTXI || op == EVOOS_CTXGET || op == EVOOS_CTXSET ||
           op == EVOOS_ADDPROT || op == EVOOS_CLRPROT;
}

static void write_reg(EvoOSContext *ctx, int32_t rd, int32_t val) {
    if (rd == 0) {
        return;
    }
    if (rd > 0 && rd < EVOOS_N_REGS) {
        ctx->regs[rd] = val;
    }
}

static void fault(EvoOSMachine *m, int cid, int32_t code) {
    if (cid == 0 && !m->supervisor_halt_on_mem &&
        (code == EVOOS_FAULT_MEM || code == EVOOS_FAULT_PROT || code == EVOOS_FAULT_CTX)) {
        m->supervisor_fault = code;
        return;
    }
    m->contexts[cid].fault = code;
    if (cid == 0) {
        m->supervisor_fault = code;
        m->halted = 1;
    }
}

static int prot_allows(EvoOSMachine *m, int cid, int32_t addr, int perm) {
    int i;
    EvoOSContext *ctx;
    if (cid == 0) {
        return 1;
    }
    ctx = &m->contexts[cid];
    for (i = 0; i < ctx->n_prot; i++) {
        if (ctx->prot_lo[i] <= addr && addr < ctx->prot_hi[i] && (ctx->prot_fl[i] & perm)) {
            return 1;
        }
    }
    return 0;
}

static int mem_load(EvoOSMachine *m, int cid, int32_t addr, int32_t *out) {
    if (addr < 0 || addr >= m->mem_words) {
        fault(m, cid, EVOOS_FAULT_MEM);
        return 0;
    }
    if (!prot_allows(m, cid, addr, EVOOS_PERM_R)) {
        fault(m, cid, EVOOS_FAULT_PROT);
        return 0;
    }
    *out = m->mem[addr];
    return 1;
}

static void mem_store(EvoOSMachine *m, int cid, int32_t addr, int32_t val) {
    if (addr < 0 || addr >= m->mem_words) {
        fault(m, cid, EVOOS_FAULT_MEM);
        return;
    }
    if (!prot_allows(m, cid, addr, EVOOS_PERM_W)) {
        fault(m, cid, EVOOS_FAULT_PROT);
        return;
    }
    if (cid != 0 && m->legal_set[cid]) {
        if (!(m->legal_lo[cid] <= addr && addr < m->legal_hi[cid])) {
            if (m->isolation_n < EVOOS_MAX_ISOLATION) {
                m->iso_cid[m->isolation_n] = cid;
                m->iso_addr[m->isolation_n] = addr;
                m->isolation_n++;
            }
        }
    }
    m->mem[addr] = val;
    m->last_writer[addr] = (uint8_t)cid;
    if (evoos_store_hook && addr >= m->io_base && addr < m->io_bound) {
        evoos_store_hook(addr, val);
    }
}

static int32_t ctx_field_get(EvoOSMachine *m, EvoOSContext *target, int32_t field) {
    if (field == EVOOS_FLD_PC) {
        return target->pc;
    }
    if (field == EVOOS_FLD_HALTED) {
        return target->halted ? 1 : 0;
    }
    if (field == EVOOS_FLD_FAULT) {
        return target->fault;
    }
    if (field == EVOOS_FLD_CYCLES) {
        return target->cycles;
    }
    if (field == EVOOS_FLD_DATA_BASE) {
        return target->data_base;
    }
    if (field == EVOOS_FLD_DATA_BOUND) {
        return target->data_bound;
    }
    if (field == EVOOS_FLD_N_CONTEXTS) {
        return m->n_contexts;
    }
    if (field == EVOOS_FLD_STATUS) {
        return (target->halted ? 1 : 0) | ((target->fault != 0) ? 2 : 0);
    }
    if (field == EVOOS_FLD_LAST_RUN_STEPS) {
        return m->runctx_posts_status ? m->last_run_steps : 0;
    }
    if (field == EVOOS_FLD_LAST_RUN_FAULT) {
        return m->runctx_posts_status ? m->last_run_fault : 0;
    }
    if (field == EVOOS_FLD_LAST_RUN_CTX) {
        return m->runctx_posts_status ? m->last_run_ctx : 0;
    }
    return 0;
}

static void ctx_field_set(EvoOSContext *target, int32_t field, int32_t val) {
    if (field == EVOOS_FLD_PC) {
        target->pc = val;
    } else if (field == EVOOS_FLD_HALTED) {
        target->halted = val ? 1 : 0;
    } else if (field == EVOOS_FLD_FAULT) {
        target->fault = val;
    }
}

static void execute(EvoOSMachine *m, int cid);

static int32_t run_context(EvoOSMachine *m, int32_t cid, int32_t n_steps) {
    int32_t ran = 0;
    EvoOSContext *guest;
    if (cid <= 0 || cid >= m->n_contexts) {
        fault(m, 0, EVOOS_FAULT_CTX);
        return 0;
    }
    if (n_steps < 0) {
        n_steps = 0;
    }
    if (n_steps > m->max_steps) {
        n_steps = m->max_steps;
    }
    guest = &m->contexts[cid];
    if (!m->sticky_guest_fault && guest->fault && !guest->halted) {
        guest->fault = 0;
    }
    while (ran < n_steps) {
        if (m->halted || m->cycles >= m->max_steps) {
            break;
        }
        if (guest->halted || guest->fault) {
            break;
        }
        execute(m, cid);
        ran++;
    }
    m->switch_count += 1;
    m->last_run_steps = ran;
    m->last_run_fault = guest->fault;
    m->last_run_ctx = cid;
    if (m->runctx_n < EVOOS_MAX_RUNLOG) {
        m->runlog_cyc[m->runctx_n] = m->cycles;
        m->runlog_cid[m->runctx_n] = cid;
        m->runlog_ran[m->runctx_n] = ran;
        m->runctx_n++;
    }
    if (evoos_runctx_hook) {
        evoos_runctx_hook(m, cid, ran);
    }
    return ran;
}

static void execute(EvoOSMachine *m, int cid) {
    EvoOSContext *ctx = &m->contexts[cid];
    EvoOSInstr ins;
    int32_t op, a, b, c, imm, next_pc;
    int32_t *regs;
    int32_t addr, val, tid, rb, rc, lo, hi;
    int32_t sh;

    if (ctx->halted || ctx->fault) {
        return;
    }
    if (ctx->pc < 0 || ctx->pc >= ctx->n_code) {
        ctx->halted = 1;
        ctx->halt_cycle = m->cycles;
        if (cid == 0) {
            m->halted = 1;
        }
        return;
    }
    ins = ctx->code[ctx->pc];
    op = ins.op;
    a = ins.a;
    b = ins.b;
    c = ins.c;
    imm = ins.imm;
    m->cycles += 1;
    ctx->cycles += 1;
    if (m->cycles > m->wall_steps) {
        m->halted = 1;
        fault(m, cid, EVOOS_FAULT_BUDGET);
        return;
    }
    regs = ctx->regs;
    next_pc = ctx->pc + 1;

    if (privileged_op(op) && cid != 0) {
        fault(m, cid, EVOOS_FAULT_PRIV);
        return;
    }

    switch (op) {
    case EVOOS_HALT:
        ctx->halted = 1;
        ctx->halt_cycle = m->cycles;
        ctx->pc = next_pc;
        if (cid == 0) {
            m->halted = 1;
        }
        return;
    case EVOOS_NOP:
        ctx->pc = next_pc;
        return;
    case EVOOS_MOV:
        write_reg(ctx, a, regs[b]);
        ctx->pc = next_pc;
        return;
    case EVOOS_MOVI:
        write_reg(ctx, a, imm);
        ctx->pc = next_pc;
        return;
    case EVOOS_ADD:
        write_reg(ctx, a, wrap32_add(regs[b], regs[c]));
        ctx->pc = next_pc;
        return;
    case EVOOS_SUB:
        write_reg(ctx, a, wrap32_sub(regs[b], regs[c]));
        ctx->pc = next_pc;
        return;
    case EVOOS_MUL:
        write_reg(ctx, a, wrap32_mul(regs[b], regs[c]));
        ctx->pc = next_pc;
        return;
    case EVOOS_AND:
        write_reg(ctx, a, regs[b] & regs[c]);
        ctx->pc = next_pc;
        return;
    case EVOOS_OR:
        write_reg(ctx, a, regs[b] | regs[c]);
        ctx->pc = next_pc;
        return;
    case EVOOS_XOR:
        write_reg(ctx, a, regs[b] ^ regs[c]);
        ctx->pc = next_pc;
        return;
    case EVOOS_SHL:
        sh = (int32_t)((uint32_t)regs[c] & 31u);
        write_reg(ctx, a, (int32_t)((uint32_t)regs[b] << sh));
        ctx->pc = next_pc;
        return;
    case EVOOS_SHR:
        sh = (int32_t)((uint32_t)regs[c] & 31u);
        write_reg(ctx, a, (int32_t)((uint32_t)regs[b] >> sh));
        ctx->pc = next_pc;
        return;
    case EVOOS_NOT:
        write_reg(ctx, a, wrap32_not(regs[b]));
        ctx->pc = next_pc;
        return;
    case EVOOS_ADDI:
        write_reg(ctx, a, wrap32_add(regs[b], imm));
        ctx->pc = next_pc;
        return;
    case EVOOS_LOAD:
        addr = wrap32_add(regs[b], imm);
        val = 0;
        mem_load(m, cid, addr, &val);
        if (ctx->fault) {
            if (cid == 0 && !m->halted) {
                ctx->pc = next_pc;
            }
            return;
        }
        write_reg(ctx, a, val);
        ctx->pc = next_pc;
        return;
    case EVOOS_STORE:
        addr = wrap32_add(regs[a], imm);
        mem_store(m, cid, addr, regs[b]);
        if (ctx->fault) {
            if (cid == 0 && !m->halted) {
                ctx->pc = next_pc;
            }
            return;
        }
        ctx->pc = next_pc;
        return;
    case EVOOS_CMP:
        rb = regs[b];
        rc = regs[c];
        if (rb < rc) {
            write_reg(ctx, a, -1);
        } else if (rb > rc) {
            write_reg(ctx, a, 1);
        } else {
            write_reg(ctx, a, 0);
        }
        ctx->pc = next_pc;
        return;
    case EVOOS_BEQ:
        ctx->pc = (regs[a] == regs[b]) ? next_pc + imm : next_pc;
        return;
    case EVOOS_BNE:
        ctx->pc = (regs[a] != regs[b]) ? next_pc + imm : next_pc;
        return;
    case EVOOS_BLT:
        ctx->pc = (regs[a] < regs[b]) ? next_pc + imm : next_pc;
        return;
    case EVOOS_BGE:
        ctx->pc = (regs[a] >= regs[b]) ? next_pc + imm : next_pc;
        return;
    case EVOOS_JMP:
        ctx->pc = next_pc + imm;
        return;
    case EVOOS_JAL:
        write_reg(ctx, a, next_pc);
        ctx->pc = next_pc + imm;
        return;
    case EVOOS_JR:
        ctx->pc = regs[a];
        return;
    case EVOOS_RUNCTX:
        ctx->pc = next_pc;
        tid = regs[a];
        if (tid > 0 && tid < m->n_contexts) {
            run_context(m, tid, regs[b]);
        }
        return;
    case EVOOS_RUNCTXI:
        ctx->pc = next_pc;
        tid = regs[a];
        if (tid > 0 && tid < m->n_contexts) {
            run_context(m, tid, imm);
        }
        return;
    case EVOOS_CTXGET:
        tid = regs[b];
        if (tid >= 0 && tid < m->n_contexts) {
            write_reg(ctx, a, ctx_field_get(m, &m->contexts[tid], imm));
        }
        ctx->pc = next_pc;
        return;
    case EVOOS_CTXSET:
        tid = regs[a];
        if (tid >= 0 && tid < m->n_contexts) {
            ctx_field_set(&m->contexts[tid], imm, regs[b]);
        }
        ctx->pc = next_pc;
        return;
    case EVOOS_ADDPROT:
        tid = regs[a];
        if (tid > 0 && tid < m->n_contexts) {
            EvoOSContext *target = &m->contexts[tid];
            if (target->n_prot < m->max_prot_regions && target->n_prot < EVOOS_MAX_PROT) {
                lo = regs[b];
                hi = regs[c];
                if (hi > lo) {
                    int n = target->n_prot;
                    target->prot_lo[n] = lo;
                    target->prot_hi[n] = hi;
                    target->prot_fl[n] = EVOOS_PERM_RW;
                    target->n_prot = n + 1;
                }
            }
        }
        ctx->pc = next_pc;
        return;
    case EVOOS_CLRPROT:
        tid = regs[a];
        if (tid > 0 && tid < m->n_contexts) {
            m->contexts[tid].n_prot = 0;
        }
        ctx->pc = next_pc;
        return;
    case EVOOS_RDTIME:
        write_reg(ctx, a, m->cycles);
        ctx->pc = next_pc;
        return;
    case EVOOS_RDFAULT:
        write_reg(ctx, a, ctx->fault);
        ctx->pc = next_pc;
        return;
    default:
        fault(m, cid, EVOOS_FAULT_BAD_OP);
        return;
    }
}

void evoos_run(EvoOSMachine *m, int32_t max_steps) {
    int32_t limit = (max_steps < 0) ? m->max_steps : max_steps;
    while (!m->halted && m->cycles < limit) {
        EvoOSContext *sup = &m->contexts[0];
        if (sup->halted || sup->fault) {
            m->halted = 1;
            break;
        }
        execute(m, 0);
    }
    if (m->cycles >= limit && !m->halted) {
        m->halted = 1;
    }
}

void evoos_run_slice(EvoOSMachine *m, int32_t n_ops) {
    int32_t end;
    if (n_ops <= 0) {
        return;
    }
    end = m->cycles + n_ops;
    if (end < m->cycles) {
        end = 0x7fffffff;
    }
    while (!m->halted && m->cycles < end) {
        EvoOSContext *sup = &m->contexts[0];
        if (sup->halted || sup->fault) {
            m->halted = 1;
            break;
        }
        execute(m, 0);
    }
}

void evoos_reenter_supervisor(EvoOSMachine *m) {
    EvoOSContext *s = &m->contexts[0];
    s->halted = 0;
    s->fault = 0;
    s->pc = 0;
    m->halted = 0;
    m->supervisor_fault = 0;
}

int evoos_install_guest(EvoOSMachine *m, int cid, const EvoOSInstr *code, int n_code,
                        int32_t base, int32_t bound, const int32_t *init_addr,
                        const int32_t *init_val, int n_init, const char *name) {
    EvoOSContext *ctx;
    int32_t a, i;
    if (cid <= 0 || cid >= m->n_contexts || n_code <= 0 || n_code > EVOOS_MAX_CODE) {
        return -1;
    }
    ctx = &m->contexts[cid];
    ctx->n_code = n_code;
    for (i = 0; i < n_code; i++) {
        ctx->code[i] = code[i];
    }
    ctx->pc = 0;
    ctx->halted = 0;
    ctx->fault = 0;
    ctx->cycles = 0;
    ctx->halt_cycle = -1;
    ctx->data_base = base;
    ctx->data_bound = bound;
    for (i = 0; i < EVOOS_N_REGS; i++) {
        ctx->regs[i] = 0;
    }
    ctx->regs[1] = base;
    ctx->regs[2] = bound;
    m->legal_lo[cid] = base;
    m->legal_hi[cid] = bound;
    m->legal_set[cid] = 1;
    if (base >= 0 && bound <= m->mem_words && bound > base) {
        for (a = base; a < bound; a++) {
            m->mem[a] = 0;
            m->last_writer[a] = 255;
        }
    }
    for (i = 0; i < n_init; i++) {
        a = init_addr[i];
        if (a >= 0 && a < m->mem_words) {
            m->mem[a] = init_val[i];
            m->last_writer[a] = 0;
        }
    }
    if (name && cid >= 1) {
        for (i = 0; i < 31; i++) {
            m->guest_name[cid - 1][i] = name[i];
            if (name[i] == 0) {
                break;
            }
        }
        m->guest_name[cid - 1][31] = 0;
    }
    return 0;
}

static void copy_cstr32(char *dst, const uint8_t *src) {
    int i;
    for (i = 0; i < 31; i++) {
        char ch = (char)src[i];
        dst[i] = ch;
        if (ch == 0) {
            return;
        }
    }
    dst[31] = 0;
}

int evoos_load_image(EvoOSMachine *m, const uint8_t *data, size_t len) {
    uint32_t magic, version, header, n_ctx, mem_words, max_steps, wall, max_prot;
    uint32_t n_regs, flags, n_code, n_mem, n_legal, n_exp, n_guests, io_base, io_bound;
    size_t off;
    uint32_t i, k;

    e_memset(m, 0, sizeof(*m));
    if (len < EVOOS_IMAGE_HEADER) {
        return -1;
    }
    magic = rd_u32(data + 0);
    version = rd_u32(data + 4);
    header = rd_u32(data + 8);
    if (magic != EVOOS_IMAGE_MAGIC || version != EVOOS_IMAGE_VERSION || header != EVOOS_IMAGE_HEADER) {
        return -2;
    }
    n_ctx = rd_u32(data + 12);
    mem_words = rd_u32(data + 16);
    max_steps = rd_u32(data + 20);
    wall = rd_u32(data + 24);
    max_prot = rd_u32(data + 28);
    n_regs = rd_u32(data + 32);
    flags = rd_u32(data + 36);
    n_code = rd_u32(data + 40);
    n_mem = rd_u32(data + 44);
    n_legal = rd_u32(data + 48);
    n_exp = rd_u32(data + 52);
    n_guests = rd_u32(data + 56);
    io_base = rd_u32(data + 60);
    io_bound = rd_u32(data + 64);
    if (n_ctx == 0 || n_ctx > EVOOS_MAX_CTX || mem_words == 0 || mem_words > EVOOS_MAX_MEM ||
        n_regs > EVOOS_N_REGS) {
        return -3;
    }
    m->n_contexts = (int32_t)n_ctx;
    m->mem_words = (int32_t)mem_words;
    m->max_steps = (int32_t)max_steps;
    m->wall_steps = (int32_t)wall;
    m->max_prot_regions = (int32_t)max_prot;
    m->n_regs = (int32_t)n_regs;
    m->sticky_guest_fault = (flags & EVOOS_FLAG_STICKY_FAULT) ? 1 : 0;
    m->supervisor_halt_on_mem = (flags & EVOOS_FLAG_SUP_HALT_MEM) ? 1 : 0;
    m->runctx_posts_status = (flags & EVOOS_FLAG_RUNCTX_STATUS) ? 1 : 0;
    m->io_base = (int32_t)io_base;
    m->io_bound = (int32_t)io_bound;
    copy_cstr32(m->origin, data + 80);
    copy_cstr32(m->workload, data + 112);
    for (i = 0; i < mem_words; i++) {
        m->last_writer[i] = 255;
    }
    for (i = 0; i < n_ctx; i++) {
        m->contexts[i].halt_cycle = -1;
        m->contexts[i].n_code = 0;
        m->contexts[i].n_prot = 0;
    }
    off = EVOOS_IMAGE_HEADER;
    {
        uint32_t ctx_id[EVOOS_MAX_CTX];
        uint32_t n_instrs[EVOOS_MAX_CTX];
        uint32_t blob_off[EVOOS_MAX_CTX];
        if (n_code > EVOOS_MAX_CTX) {
            return -4;
        }
        for (i = 0; i < n_code; i++) {
            if (off + 16 > len) {
                return -5;
            }
            ctx_id[i] = rd_u32(data + off);
            n_instrs[i] = rd_u32(data + off + 4);
            blob_off[i] = rd_u32(data + off + 8);
            off += 16;
        }
        for (i = 0; i < n_mem; i++) {
            int32_t addr, val;
            if (off + 8 > len) {
                return -6;
            }
            addr = rd_i32(data + off);
            val = rd_i32(data + off + 4);
            off += 8;
            if (addr >= 0 && addr < m->mem_words) {
                m->mem[addr] = val;
                m->last_writer[addr] = 0;
            }
        }
        for (i = 0; i < n_legal; i++) {
            uint32_t cid;
            int32_t lo, hi;
            if (off + 12 > len) {
                return -7;
            }
            cid = rd_u32(data + off);
            lo = rd_i32(data + off + 4);
            hi = rd_i32(data + off + 8);
            off += 12;
            if (cid < n_ctx) {
                m->legal_lo[cid] = lo;
                m->legal_hi[cid] = hi;
                m->legal_set[cid] = 1;
            }
        }
        if (n_exp > 256) {
            n_exp = 256;
        }
        for (i = 0; i < n_exp; i++) {
            if (off + 16 > len) {
                return -8;
            }
            m->exp_cid[i] = (int32_t)rd_u32(data + off);
            m->exp_addr[i] = rd_i32(data + off + 4);
            m->exp_val[i] = rd_i32(data + off + 8);
            off += 16;
        }
        m->n_expected = (int32_t)n_exp;
        if (n_guests > EVOOS_MAX_CTX) {
            n_guests = EVOOS_MAX_CTX;
        }
        m->n_guests = (int32_t)n_guests;
        for (i = 0; i < n_guests; i++) {
            if (off + 32 > len) {
                return -9;
            }
            copy_cstr32(m->guest_name[i], data + off);
            off += 32;
        }
        for (i = 0; i < n_code; i++) {
            uint32_t cid = ctx_id[i];
            uint32_t ni = n_instrs[i];
            uint32_t bo = blob_off[i];
            EvoOSContext *ctx;
            if (cid >= n_ctx || ni > EVOOS_MAX_CODE) {
                return -10;
            }
            if ((size_t)bo + (size_t)ni * 20u > len) {
                return -11;
            }
            ctx = &m->contexts[cid];
            ctx->n_code = (int32_t)ni;
            for (k = 0; k < ni; k++) {
                const uint8_t *p = data + bo + k * 20u;
                ctx->code[k].op = rd_i32(p);
                ctx->code[k].a = rd_i32(p + 4);
                ctx->code[k].b = rd_i32(p + 8);
                ctx->code[k].c = rd_i32(p + 12);
                ctx->code[k].imm = rd_i32(p + 16);
            }
            ctx->pc = 0;
            ctx->halted = 0;
            ctx->fault = 0;
            ctx->cycles = 0;
            ctx->halt_cycle = -1;
            ctx->n_prot = 0;
            e_memset(ctx->regs, 0, sizeof(ctx->regs));
            if (cid == 0) {
                ctx->data_base = 0;
                ctx->data_bound = m->mem_words;
            } else if (m->legal_set[cid]) {
                ctx->data_base = m->legal_lo[cid];
                ctx->data_bound = m->legal_hi[cid];
                ctx->regs[1] = ctx->data_base;
                ctx->regs[2] = ctx->data_bound;
            }
        }
    }
    return 0;
}

uint32_t evoos_mem_fingerprint(const EvoOSMachine *m) {
    uint32_t h = 2166136261u;
    int32_t i;
    for (i = 0; i < m->mem_words; i++) {
        h ^= (uint32_t)m->mem[i];
        h *= 16777619u;
    }
    return h;
}

static char *i32_to(char *p, int32_t v) {
    char tmp[16];
    int n = 0;
    uint32_t u;
    if (v < 0) {
        *p++ = '-';
        u = (uint32_t)(-(int64_t)v);
    } else {
        u = (uint32_t)v;
    }
    do {
        tmp[n++] = (char)('0' + (u % 10u));
        u /= 10u;
    } while (u);
    while (n--) {
        *p++ = tmp[n];
    }
    return p;
}

static char *u32_to(char *p, uint32_t v) {
    char tmp[16];
    int n = 0;
    do {
        tmp[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    } while (v);
    while (n--) {
        *p++ = tmp[n];
    }
    return p;
}

static char *cpy(char *p, const char *s) {
    while (*s) {
        *p++ = *s++;
    }
    return p;
}

void evoos_dump_trace(const EvoOSMachine *m, evoos_emit_fn emit) {
    char line[512];
    char *p;
    int32_t i, j;

    emit("# evoos-trace v1");

    p = cpy(line, "cycles=");
    p = i32_to(p, m->cycles);
    *p = 0;
    emit(line);

    p = cpy(line, "switch_count=");
    p = i32_to(p, m->switch_count);
    *p = 0;
    emit(line);

    p = cpy(line, "halted=");
    p = i32_to(p, m->halted ? 1 : 0);
    *p = 0;
    emit(line);

    p = cpy(line, "supervisor_fault=");
    p = i32_to(p, m->supervisor_fault);
    *p = 0;
    emit(line);

    p = cpy(line, "last_run_steps=");
    p = i32_to(p, m->last_run_steps);
    *p = 0;
    emit(line);

    p = cpy(line, "last_run_fault=");
    p = i32_to(p, m->last_run_fault);
    *p = 0;
    emit(line);

    p = cpy(line, "last_run_ctx=");
    p = i32_to(p, m->last_run_ctx);
    *p = 0;
    emit(line);

    p = cpy(line, "isolation_n=");
    p = i32_to(p, m->isolation_n);
    *p = 0;
    emit(line);
    for (i = 0; i < m->isolation_n; i++) {
        p = cpy(line, "isolation ");
        p = i32_to(p, m->iso_cid[i]);
        *p++ = ',';
        p = i32_to(p, m->iso_addr[i]);
        *p = 0;
        emit(line);
    }

    for (i = 0; i < m->n_contexts; i++) {
        const EvoOSContext *ctx = &m->contexts[i];
        p = cpy(line, "ctx ");
        p = i32_to(p, i);
        p = cpy(p, " pc=");
        p = i32_to(p, ctx->pc);
        p = cpy(p, " halted=");
        p = i32_to(p, ctx->halted ? 1 : 0);
        p = cpy(p, " fault=");
        p = i32_to(p, ctx->fault);
        p = cpy(p, " cycles=");
        p = i32_to(p, ctx->cycles);
        p = cpy(p, " halt_cycle=");
        p = i32_to(p, ctx->halt_cycle);
        p = cpy(p, " base=");
        p = i32_to(p, ctx->data_base);
        p = cpy(p, " bound=");
        p = i32_to(p, ctx->data_bound);
        p = cpy(p, " regs=");
        for (j = 0; j < m->n_regs; j++) {
            if (j) {
                *p++ = ',';
            }
            p = i32_to(p, ctx->regs[j]);
        }
        p = cpy(p, " prot=");
        for (j = 0; j < ctx->n_prot; j++) {
            if (j) {
                *p++ = ',';
            }
            p = i32_to(p, ctx->prot_lo[j]);
            *p++ = ':';
            p = i32_to(p, ctx->prot_hi[j]);
            *p++ = ':';
            p = i32_to(p, ctx->prot_fl[j]);
        }
        *p = 0;
        emit(line);
    }

    p = cpy(line, "memfp=");
    p = u32_to(p, evoos_mem_fingerprint(m));
    *p = 0;
    emit(line);

    p = cpy(line, "runctx_n=");
    p = i32_to(p, m->runctx_n);
    *p = 0;
    emit(line);
    for (i = 0; i < m->runctx_n; i++) {
        p = cpy(line, "runctx ");
        p = i32_to(p, m->runlog_cyc[i]);
        *p++ = ',';
        p = i32_to(p, m->runlog_cid[i]);
        *p++ = ',';
        p = i32_to(p, m->runlog_ran[i]);
        *p = 0;
        emit(line);
    }
}
