#include "evoos_vm.h"
#include "guest_catalogue.h"
#include "limine.h"

#include <stddef.h>
#include <stdint.h>

__attribute__((used, section(".limine_requests")))
static volatile LIMINE_BASE_REVISION(3);

__attribute__((used, section(".limine_requests")))
static volatile struct limine_framebuffer_request framebuffer_request = {
    .id = LIMINE_FRAMEBUFFER_REQUEST,
    .revision = 0};

__attribute__((used, section(".limine_requests")))
static volatile struct limine_hhdm_request hhdm_request = {
    .id = LIMINE_HHDM_REQUEST,
    .revision = 0};

__attribute__((used, section(".limine_requests_start")))
static volatile LIMINE_REQUESTS_START_MARKER;

__attribute__((used, section(".limine_requests_end")))
static volatile LIMINE_REQUESTS_END_MARKER;

extern const uint8_t evoos_image[];
extern const uint8_t evoos_image_end[];

static struct limine_framebuffer *fb;
static uint32_t fb_white, fb_bg, fb_accent, fb_ok, fb_bad, fb_idle, fb_dim;
static uint32_t fb_guest[5];

static inline void outb(uint16_t port, uint8_t val) {
    __asm__ volatile("outb %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint8_t inb(uint16_t port) {
    uint8_t r;
    __asm__ volatile("inb %1, %0" : "=a"(r) : "Nd"(port));
    return r;
}

static void serial_init(void) {
    outb(0x3F8 + 1, 0x00);
    outb(0x3F8 + 3, 0x80);
    outb(0x3F8 + 0, 0x03);
    outb(0x3F8 + 1, 0x00);
    outb(0x3F8 + 3, 0x03);
    outb(0x3F8 + 2, 0xC7);
    outb(0x3F8 + 4, 0x0B);
}

static void serial_putc(char c) {
    int spins = 100000;
    while (spins-- && !(inb(0x3F8 + 5) & 0x20)) {
    }
    outb(0x3F8, (uint8_t)c);
}

static uint32_t fb_pixel(struct limine_framebuffer *f, uint8_t r, uint8_t g, uint8_t b) {
    uint32_t pix = 0;
    uint64_t max;
    if (!f) {
        return 0;
    }
    max = ((uint64_t)1 << f->red_mask_size) - 1;
    pix |= (uint32_t)((r * max / 255) << f->red_mask_shift);
    max = ((uint64_t)1 << f->green_mask_size) - 1;
    pix |= (uint32_t)((g * max / 255) << f->green_mask_shift);
    max = ((uint64_t)1 << f->blue_mask_size) - 1;
    pix |= (uint32_t)((b * max / 255) << f->blue_mask_shift);
    return pix;
}

static void fb_fill(uint32_t color) {
    size_t y, x;
    if (!fb || fb->bpp != 32) {
        return;
    }
    for (y = 0; y < fb->height; y++) {
        volatile uint32_t *row = (volatile uint32_t *)((uint8_t *)fb->address + y * fb->pitch);
        for (x = 0; x < fb->width; x++) {
            row[x] = color;
        }
    }
}

static void fb_rect(int x, int y, int w, int h, uint32_t color) {
    int yy, xx;
    if (!fb || fb->bpp != 32) {
        return;
    }
    for (yy = y; yy < y + h; yy++) {
        volatile uint32_t *row;
        if (yy < 0 || (size_t)yy >= fb->height) {
            continue;
        }
        row = (volatile uint32_t *)((uint8_t *)fb->address + (size_t)yy * fb->pitch);
        for (xx = x; xx < x + w; xx++) {
            if (xx >= 0 && (size_t)xx < fb->width) {
                row[xx] = color;
            }
        }
    }
}

/* 8x8 font for ASCII 32..90 (space through Z). Bit7 is the leftmost pixel. */
static const uint8_t font8[][8] = {
    {0, 0, 0, 0, 0, 0, 0, 0}, /* space */
    {0x18, 0x18, 0x18, 0x18, 0x18, 0x00, 0x18, 0x00},
    {0x6c, 0x6c, 0x24, 0x00, 0x00, 0x00, 0x00, 0x00},
    {0x6c, 0x6c, 0xfe, 0x6c, 0xfe, 0x6c, 0x6c, 0x00},
    {0x18, 0x3e, 0x60, 0x3c, 0x06, 0x7c, 0x18, 0x00},
    {0x62, 0x66, 0x0c, 0x18, 0x30, 0x66, 0x46, 0x00},
    {0x38, 0x6c, 0x38, 0x76, 0xdc, 0xcc, 0x76, 0x00},
    {0x18, 0x18, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00},
    {0x0c, 0x18, 0x30, 0x30, 0x30, 0x18, 0x0c, 0x00},
    {0x30, 0x18, 0x0c, 0x0c, 0x0c, 0x18, 0x30, 0x00},
    {0x00, 0x66, 0x3c, 0xff, 0x3c, 0x66, 0x00, 0x00},
    {0x00, 0x18, 0x18, 0x7e, 0x18, 0x18, 0x00, 0x00},
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x30},
    {0x00, 0x00, 0x00, 0x7e, 0x00, 0x00, 0x00, 0x00},
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00},
    {0x02, 0x06, 0x0c, 0x18, 0x30, 0x60, 0x40, 0x00},
    {0x3c, 0x66, 0x6e, 0x76, 0x66, 0x66, 0x3c, 0x00}, /* 0 */
    {0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x7e, 0x00},
    {0x3c, 0x66, 0x06, 0x0c, 0x18, 0x30, 0x7e, 0x00},
    {0x3c, 0x66, 0x06, 0x1c, 0x06, 0x66, 0x3c, 0x00},
    {0x0c, 0x1c, 0x3c, 0x6c, 0x7e, 0x0c, 0x0c, 0x00},
    {0x7e, 0x60, 0x7c, 0x06, 0x06, 0x66, 0x3c, 0x00},
    {0x1c, 0x30, 0x60, 0x7c, 0x66, 0x66, 0x3c, 0x00},
    {0x7e, 0x06, 0x0c, 0x18, 0x30, 0x30, 0x30, 0x00},
    {0x3c, 0x66, 0x66, 0x3c, 0x66, 0x66, 0x3c, 0x00},
    {0x3c, 0x66, 0x66, 0x3e, 0x06, 0x0c, 0x38, 0x00},
    {0x00, 0x18, 0x18, 0x00, 0x00, 0x18, 0x18, 0x00},
    {0x00, 0x18, 0x18, 0x00, 0x00, 0x18, 0x18, 0x30},
    {0x0c, 0x18, 0x30, 0x60, 0x30, 0x18, 0x0c, 0x00},
    {0x00, 0x00, 0x7e, 0x00, 0x7e, 0x00, 0x00, 0x00},
    {0x30, 0x18, 0x0c, 0x06, 0x0c, 0x18, 0x30, 0x00},
    {0x3c, 0x66, 0x06, 0x0c, 0x18, 0x00, 0x18, 0x00},
    {0x3c, 0x66, 0x6e, 0x6e, 0x60, 0x62, 0x3c, 0x00},
    {0x18, 0x3c, 0x66, 0x66, 0x7e, 0x66, 0x66, 0x00}, /* A */
    {0x7c, 0x66, 0x66, 0x7c, 0x66, 0x66, 0x7c, 0x00},
    {0x3c, 0x66, 0x60, 0x60, 0x60, 0x66, 0x3c, 0x00},
    {0x78, 0x6c, 0x66, 0x66, 0x66, 0x6c, 0x78, 0x00},
    {0x7e, 0x60, 0x60, 0x7c, 0x60, 0x60, 0x7e, 0x00},
    {0x7e, 0x60, 0x60, 0x7c, 0x60, 0x60, 0x60, 0x00},
    {0x3c, 0x66, 0x60, 0x6e, 0x66, 0x66, 0x3c, 0x00},
    {0x66, 0x66, 0x66, 0x7e, 0x66, 0x66, 0x66, 0x00},
    {0x7e, 0x18, 0x18, 0x18, 0x18, 0x18, 0x7e, 0x00},
    {0x06, 0x06, 0x06, 0x06, 0x06, 0x66, 0x3c, 0x00},
    {0x66, 0x6c, 0x78, 0x70, 0x78, 0x6c, 0x66, 0x00},
    {0x60, 0x60, 0x60, 0x60, 0x60, 0x60, 0x7e, 0x00},
    {0x63, 0x77, 0x7f, 0x6b, 0x63, 0x63, 0x63, 0x00},
    {0x66, 0x76, 0x7e, 0x7e, 0x6e, 0x66, 0x66, 0x00},
    {0x3c, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3c, 0x00},
    {0x7c, 0x66, 0x66, 0x7c, 0x60, 0x60, 0x60, 0x00},
    {0x3c, 0x66, 0x66, 0x66, 0x6a, 0x6c, 0x36, 0x00},
    {0x7c, 0x66, 0x66, 0x7c, 0x6c, 0x66, 0x66, 0x00},
    {0x3c, 0x66, 0x60, 0x3c, 0x06, 0x66, 0x3c, 0x00},
    {0x7e, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x00},
    {0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3c, 0x00},
    {0x66, 0x66, 0x66, 0x66, 0x66, 0x3c, 0x18, 0x00},
    {0x63, 0x63, 0x63, 0x6b, 0x7f, 0x77, 0x63, 0x00},
    {0x66, 0x66, 0x3c, 0x18, 0x3c, 0x66, 0x66, 0x00},
    {0x66, 0x66, 0x66, 0x3c, 0x18, 0x18, 0x18, 0x00},
    {0x7e, 0x06, 0x0c, 0x18, 0x30, 0x60, 0x7e, 0x00},
};

static void fb_glyph(int x, int y, char ch, uint32_t color, uint32_t bg) {
    int row, col, idx;
    const uint8_t *g;
    if (ch >= 'a' && ch <= 'z') {
        ch = (char)(ch - 32);
    }
    if (ch == '_') {
        ch = '-';
    }
    if (ch < 32 || ch > 90) {
        ch = '?';
    }
    idx = ch - 32;
    g = font8[idx];
    for (row = 0; row < 8; row++) {
        uint8_t bits = g[row];
        for (col = 0; col < 8; col++) {
            fb_rect(x + col * 2, y + row * 2, 2, 2, (bits & (0x80 >> col)) ? color : bg);
        }
    }
}

static void cons_putc(char c) {
    if (c == '\n') {
        serial_putc('\r');
        serial_putc('\n');
        return;
    }
    if (c != '\r') {
        serial_putc(c);
    }
}

static void fb_putc_at(int x, int y, char c, uint32_t color) { fb_glyph(x, y, c, color, fb_bg); }

static void fb_puts_at(int x, int y, const char *s, uint32_t color) {
    while (*s) {
        fb_putc_at(x, y, *s++, color);
        x += 16;
    }
}

static void fb_i32_at(int x, int y, int32_t v, uint32_t color) {
    char buf[16];
    char *p = buf + 15;
    uint32_t u;
    *p = 0;
    if (v < 0) {
        u = (uint32_t)(-(int64_t)v);
        do {
            *--p = (char)('0' + (u % 10u));
            u /= 10u;
        } while (u);
        *--p = '-';
    } else {
        u = (uint32_t)v;
        do {
            *--p = (char)('0' + (u % 10u));
            u /= 10u;
        } while (u);
    }
    fb_puts_at(x, y, p, color);
}

static void cons_puts(const char *s) {
    while (*s) {
        cons_putc(*s++);
    }
}

static void cons_i32(int32_t v) {
    char buf[16];
    char *p = buf + 15;
    uint32_t u;
    *p = 0;
    if (v < 0) {
        u = (uint32_t)(-(int64_t)v);
        do {
            *--p = (char)('0' + (u % 10u));
            u /= 10u;
        } while (u);
        *--p = '-';
    } else {
        u = (uint32_t)v;
        do {
            *--p = (char)('0' + (u % 10u));
            u /= 10u;
        } while (u);
    }
    cons_puts(p);
}

static void qemu_exit(uint8_t code) {
    outb(0xF4, code);
    for (;;) {
        __asm__ volatile("hlt");
    }
}

#define HEAT_N 320
static EvoOSMachine machine;
static int32_t seen_halt[EVOOS_MAX_CTX];
static uint8_t heat_cid[HEAT_N];
static uint8_t heat_ran[HEAT_N];
static int heat_i;
static int heat_fill;
static int32_t spawn_count;
static int32_t key_seq;
static int32_t fault_events;
static int want_redraw = 1;
static int rotating_kind;

static const char *KINDS[] = {"sum", "fill", "xor", "copyinc", "max", "malicious"};
#define N_KINDS 6

static void io_poke(int32_t off, int32_t val) {
    int32_t addr = machine.io_base + off;
    if (addr >= 0 && addr < machine.mem_words) {
        machine.mem[addr] = val;
    }
}

static void on_runctx(const EvoOSMachine *m, int32_t cid, int32_t ran) {
    const EvoOSContext *g;
    if (cid <= 0 || cid >= m->n_contexts) {
        return;
    }
    heat_cid[heat_i] = (uint8_t)cid;
    heat_ran[heat_i] = (uint8_t)(ran > 255 ? 255 : ran);
    heat_i = (heat_i + 1) % HEAT_N;
    if (heat_fill < HEAT_N) {
        heat_fill++;
    }
    g = &m->contexts[cid];
    if (g->fault && !seen_halt[cid]) {
        seen_halt[cid] = 1;
        fault_events++;
        cons_puts("guest ");
        cons_i32(cid);
        cons_puts(" ");
        cons_puts(m->guest_name[cid - 1]);
        cons_puts(" fault=");
        cons_i32(g->fault);
        cons_puts(" isolation_n=");
        cons_i32(m->isolation_n);
        cons_putc('\n');
        want_redraw = 1;
        return;
    }
    if (g->halted && !seen_halt[cid]) {
        seen_halt[cid] = 1;
        cons_puts("guest ");
        cons_i32(cid);
        cons_puts(" ");
        cons_puts(m->guest_name[cid - 1]);
        cons_puts(" halted cycles=");
        cons_i32(g->cycles);
        cons_putc('\n');
        want_redraw = 1;
    }
}

static void draw_logo(int x, int y) {
    uint32_t c = fb_accent;
    fb_rect(x, y, 10, 36, c);
    fb_rect(x, y, 28, 8, c);
    fb_rect(x, y + 14, 22, 7, c);
    fb_rect(x, y + 28, 28, 8, c);
    fb_rect(x + 40, y, 10, 36, c);
    fb_rect(x + 68, y, 10, 36, c);
    fb_rect(x + 50, y + 12, 10, 12, c);
    fb_rect(x + 90, y, 28, 8, c);
    fb_rect(x + 90, y, 10, 36, c);
    fb_rect(x + 108, y, 10, 36, c);
    fb_rect(x + 90, y + 28, 28, 8, c);
}

static void dashboard(void) {
    int i, col, n;
    int hx, hy, hw, hh;
    if (!fb) {
        return;
    }
    draw_logo(24, 16);
    fb_puts_at(170, 16, "EVOOS LIVE", fb_accent);
    fb_puts_at(170, 36, machine.origin, fb_white);
    fb_puts_at(24, 60, "FIRMWARE=I/O PLUMBING   POLICY=BYTECODE", fb_dim);

    for (i = 1; i < machine.n_contexts && i <= 4; i++) {
        EvoOSContext *g = &machine.contexts[i];
        int y = 88 + (i - 1) * 36;
        uint32_t colr = fb_guest[i];
        const char *st = "RUN";
        if (g->fault) {
            st = "FAULT";
            colr = fb_bad;
        } else if (g->halted) {
            st = "HALT";
            colr = fb_ok;
        } else if (g->cycles == 0) {
            st = "IDLE";
            colr = fb_idle;
        }
        fb_rect(24, y, 12, 28, colr);
        fb_puts_at(44, y, "CTX", fb_dim);
        fb_i32_at(96, y, i, fb_white);
        fb_puts_at(128, y, "                ", fb_dim);
        fb_puts_at(128, y, machine.guest_name[i - 1], fb_white);
        fb_puts_at(400, y, "        ", fb_dim);
        fb_puts_at(400, y, st, colr);
        fb_puts_at(520, y, "CYC", fb_dim);
        fb_i32_at(576, y, g->cycles, fb_white);
        fb_puts_at(688, y, "FLT", fb_dim);
        fb_i32_at(744, y, g->fault, g->fault ? fb_bad : fb_dim);
    }

    hx = 24;
    hy = 250;
    hw = fb && fb->width > 80 ? (int)fb->width - 48 : 640;
    if (hw > HEAT_N * 3) {
        hw = HEAT_N * 3;
    }
    hh = 48;
    fb_puts_at(hx, hy - 20, "RUNCTX HEATMAP (TIME ->)", fb_dim);
    fb_rect(hx, hy, hw, hh, fb_idle);
    n = heat_fill;
    for (col = 0; col < n; col++) {
        int idx = (heat_i - n + col + HEAT_N) % HEAT_N;
        int cid = heat_cid[idx];
        int px = hx + (col * hw) / (n < 8 ? 8 : n);
        int pw = hw / (n < 8 ? 8 : n);
        if (pw < 1) {
            pw = 1;
        }
        fb_rect(px, hy, pw, hh, (cid >= 1 && cid <= 4) ? fb_guest[cid] : fb_dim);
    }

    fb_puts_at(24, 314, "SWITCHES", fb_dim);
    fb_i32_at(180, 314, machine.switch_count, fb_white);
    fb_puts_at(320, 314, "ISOLATION", fb_dim);
    fb_i32_at(500, 314, machine.isolation_n, machine.isolation_n ? fb_bad : fb_ok);
    fb_puts_at(24, 336, "SPAWNS", fb_dim);
    fb_i32_at(180, 336, spawn_count, fb_white);
    fb_puts_at(320, 336, "FAULTS", fb_dim);
    fb_i32_at(500, 336, fault_events, fault_events ? fb_bad : fb_white);
    fb_puts_at(24, 368, "1-4 SLOT  S F X C M KIND  I INSPECT  H HELP  Q QUIT", fb_accent);
    want_redraw = 0;
}

static void print_help(void) {
    cons_puts("keys: 1-4 spawn into slot  s/f/x/c/m kind  space next  i inspect  h help  q quit\n");
    cons_puts("loader is firmware; ADDPROT/RUNCTX stay in supervisor bytecode\n");
}

static void inspect(void) {
    int32_t i;
    cons_puts("inspect isolation_n=");
    cons_i32(machine.isolation_n);
    cons_puts(" switches=");
    cons_i32(machine.switch_count);
    cons_puts(" cycles=");
    cons_i32(machine.cycles);
    cons_putc('\n');
    for (i = 1; i < machine.n_contexts; i++) {
        EvoOSContext *g = &machine.contexts[i];
        cons_puts("  ctx ");
        cons_i32(i);
        cons_puts(" ");
        cons_puts(machine.guest_name[i - 1]);
        cons_puts(" halt=");
        cons_i32(g->halted);
        cons_puts(" fault=");
        cons_i32(g->fault);
        cons_puts(" cyc=");
        cons_i32(g->cycles);
        cons_puts(" prot=");
        cons_i32(g->n_prot);
        cons_putc('\n');
    }
}

static void loader_spawn(int slot, const char *kind) {
    int idx;
    const CatProg *p;
    int cid;
    if (slot < 0 || slot > 3) {
        return;
    }
    idx = cat_find(kind, slot);
    if (idx < 0) {
        cons_puts("loader: unknown kind\n");
        return;
    }
    p = &CAT_PROGS[idx];
    cid = slot + 1;
    if (evoos_install_guest(&machine, cid, p->code, p->n_code, p->data_base, p->data_bound, p->init_addr,
                            p->init_val, p->n_init, p->name) != 0) {
        cons_puts("loader: install failed\n");
        return;
    }
    seen_halt[cid] = 0;
    spawn_count++;
    io_poke(EVOOS_IO_SPAWNS, spawn_count);
    io_poke(EVOOS_IO_LAST_SLOT, slot);
    cons_puts("loader: slot ");
    cons_i32(slot);
    cons_puts(" ctx ");
    cons_i32(cid);
    cons_puts(" ");
    cons_puts(kind);
    cons_puts(" ");
    cons_puts(p->name);
    cons_putc('\n');
    /* Install only. Who runs is still RUNCTX in supervisor bytecode.
       Re-entry happens in kmain if the supervisor HALTed. */
    want_redraw = 1;
}

static char scancode_to_ascii(uint8_t sc) {
    switch (sc) {
    case 0x02:
        return '1';
    case 0x03:
        return '2';
    case 0x04:
        return '3';
    case 0x05:
        return '4';
    case 0x10:
        return 'q';
    case 0x1f:
        return 's';
    case 0x21:
        return 'f';
    case 0x2d:
        return 'x';
    case 0x2e:
        return 'c';
    case 0x32:
        return 'm';
    case 0x17:
        return 'i';
    case 0x23:
        return 'h';
    case 0x39:
        return ' ';
    default:
        return 0;
    }
}

static void handle_key(char ch, uint8_t scan) {
    int slot;
    static int next_slot;
    if (!ch) {
        return;
    }
    key_seq++;
    io_poke(EVOOS_IO_KEY_ASCII, (int32_t)(uint8_t)ch);
    io_poke(EVOOS_IO_KEY_SEQ, key_seq);
    io_poke(EVOOS_IO_KEY_SCAN, scan);
    if (ch >= '1' && ch <= '4') {
        loader_spawn(ch - '1', KINDS[rotating_kind % N_KINDS]);
        rotating_kind++;
        return;
    }
    switch (ch) {
    case 's':
    case 'f':
    case 'x':
    case 'c':
    case 'm': {
        const char *kind = "sum";
        if (ch == 'f') {
            kind = "fill";
        } else if (ch == 'x') {
            kind = "xor";
        } else if (ch == 'c') {
            kind = "copyinc";
        } else if (ch == 'm') {
            kind = "malicious";
        }
        slot = next_slot % 4;
        next_slot++;
        loader_spawn(slot, kind);
        break;
    }
    case ' ':
        loader_spawn(next_slot % 4, KINDS[rotating_kind % N_KINDS]);
        next_slot++;
        rotating_kind++;
        break;
    case 'i':
        inspect();
        break;
    case 'h':
        print_help();
        break;
    case 'q':
        cons_puts("EvoOS quit isolation_n=");
        cons_i32(machine.isolation_n);
        cons_puts(" spawns=");
        cons_i32(spawn_count);
        cons_putc('\n');
        qemu_exit(machine.isolation_n == 0 ? 0 : 1);
        break;
    default:
        break;
    }
}

static void poll_serial(void) {
    if (inb(0x3F8 + 5) & 0x01) {
        char ch = (char)(inb(0x3F8) & 0x7F);
        if (ch >= 'A' && ch <= 'Z') {
            ch = (char)(ch + 32);
        }
        handle_key(ch, 0);
    }
}

static void poll_ps2(void) {
    uint8_t st, sc;
    st = inb(0x64);
    if (!(st & 1)) {
        return;
    }
    sc = inb(0x60);
    if (sc & 0x80) {
        return;
    }
    handle_key(scancode_to_ascii(sc), sc);
}

void kmain(void) {
    size_t img_len;
    int rc;
    int32_t i;
    int slices = 0;
    static volatile uint32_t keep_data = 0x45564F53;

    (void)keep_data;
    serial_init();
    cons_puts("EvoOS firmware starting\n");

    if (framebuffer_request.response && framebuffer_request.response->framebuffer_count > 0) {
        fb = framebuffer_request.response->framebuffers[0];
        if (fb->bpp != 32 || fb->memory_model != LIMINE_FRAMEBUFFER_RGB) {
            fb = 0;
        } else {
            fb_bg = fb_pixel(fb, 6, 8, 20);
            fb_white = fb_pixel(fb, 230, 230, 240);
            fb_accent = fb_pixel(fb, 80, 200, 255);
            fb_ok = fb_pixel(fb, 40, 200, 90);
            fb_bad = fb_pixel(fb, 220, 60, 60);
            fb_idle = fb_pixel(fb, 28, 32, 48);
            fb_dim = fb_pixel(fb, 120, 130, 150);
            fb_guest[1] = fb_pixel(fb, 0, 210, 255);
            fb_guest[2] = fb_pixel(fb, 255, 80, 180);
            fb_guest[3] = fb_pixel(fb, 255, 190, 40);
            fb_guest[4] = fb_pixel(fb, 80, 255, 120);
        }
    }

    img_len = (size_t)(evoos_image_end - evoos_image);
    rc = evoos_load_image(&machine, evoos_image, img_len);
    if (rc != 0) {
        cons_puts("image load failed: ");
        cons_i32(rc);
        cons_putc('\n');
        qemu_exit(1);
    }

    cons_puts("EvoOS live\n");
    cons_puts("firmware is not a kernel; supervisor=");
    cons_puts(machine.origin);
    cons_putc('\n');
    print_help();

    evoos_runctx_hook = on_runctx;
    if (fb) {
        fb_fill(fb_bg);
    }
    dashboard();

    for (;;) {
        poll_ps2();
        poll_serial();
        if (machine.contexts[0].halted || machine.halted) {
            evoos_reenter_supervisor(&machine);
        }
        evoos_run_slice(&machine, 64);
        slices++;
        if (want_redraw || (slices & 15) == 0) {
            dashboard();
        }
        io_poke(EVOOS_IO_LAST_FAULT, fault_events);
        (void)i;
    }
}
