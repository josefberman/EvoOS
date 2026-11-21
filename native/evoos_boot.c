#include "hw.h"
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

__attribute__((used, section(".limine_requests")))
static volatile struct limine_kernel_address_request kernel_address_request = {
    .id = LIMINE_KERNEL_ADDRESS_REQUEST,
    .revision = 0};

__attribute__((used, section(".limine_requests_start")))
static volatile LIMINE_REQUESTS_START_MARKER;

__attribute__((used, section(".limine_requests_end")))
static volatile LIMINE_REQUESTS_END_MARKER;

extern const uint8_t guest_sum[], guest_sum_end[];
extern const uint8_t guest_fill[], guest_fill_end[];
extern const uint8_t guest_xor[], guest_xor_end[];
extern const uint8_t guest_copyinc[], guest_copyinc_end[];
extern const uint8_t guest_max[], guest_max_end[];
extern const uint8_t guest_malicious[], guest_malicious_end[];
extern const uint8_t guest_spin[], guest_spin_end[];
extern const uint8_t guest_put[], guest_put_end[];
extern const uint8_t guest_get[], guest_get_end[];

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
static int32_t seen_halt[HW_N_CTX];
static uint8_t heat_cid[HEAT_N];
static uint8_t heat_ran[HEAT_N];
static int heat_i;
static int heat_fill;
static int32_t spawn_count;
static int32_t key_seq;
static int32_t fault_events;
static int want_redraw = 1;
static int rotating_kind;
static int drawn;

static const char *KINDS[] = {"sum", "fill", "xor", "copyinc", "max", "malicious", "spin"};
#define N_KINDS 7

static void io_poke(int32_t off, int32_t val) { evoos_hw_store(1920 + off, val); }

static void note_runctx(int32_t cid, int32_t ran, int32_t fault, int32_t vector) {
    HwContext *g;
    if (cid <= 0 || cid >= hw.n_contexts) {
        return;
    }
    heat_cid[heat_i] = (uint8_t)cid;
    heat_ran[heat_i] = (uint8_t)(ran > 255 ? 255 : ran);
    heat_i = (heat_i + 1) % HEAT_N;
    if (heat_fill < HEAT_N) {
        heat_fill++;
    }
    g = &hw.ctx[cid];
    if (vector == HW_VEC_TIMER && g->preempts == 1) {
        cons_puts("guest ");
        cons_i32(cid);
        cons_puts(" ");
        cons_puts(g->name);
        cons_puts(" preempt vector=32\n");
        want_redraw = 1;
    }
    if (g->fault && !seen_halt[cid]) {
        seen_halt[cid] = 1;
        fault_events++;
        cons_puts("guest ");
        cons_i32(cid);
        cons_puts(" ");
        cons_puts(g->name);
        cons_puts(" fault=");
        cons_i32(fault);
        cons_puts(" vector=");
        cons_i32(vector);
        cons_puts(" cr2=");
        cons_i32((int32_t)g->last_cr2);
        if (vector == HW_VEC_PF) {
            cons_puts(" #PF");
        }
        cons_puts(" isolation_n=");
        cons_i32(hw.isolation_n);
        cons_putc('\n');
        want_redraw = 1;
        return;
    }
    if (g->halted && !seen_halt[cid]) {
        seen_halt[cid] = 1;
        cons_puts("guest ");
        cons_i32(cid);
        cons_puts(" ");
        cons_puts(g->name);
        cons_puts(" halted cycles=");
        cons_i32(g->cycles);
        if (g->name[0] == 'p' && g->name[1] == 'u') {
            cons_puts(" put=");
            cons_i32(evoos_hw_word(cid - 1, 2));
        }
        if (g->name[0] == 'g' && g->name[1] == 'e') {
            cons_puts(" recall=");
            cons_i32(evoos_hw_word(cid - 1, 2));
        }
        cons_putc('\n');
        want_redraw = 1;
    }
    (void)ran;
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
    int hx, hy, barw, hh;
    if (!fb) {
        return;
    }
    draw_logo(24, 16);
    fb_puts_at(170, 16, "EVOOS LIVE", fb_accent);
    fb_puts_at(170, 36, supervisor_origin, fb_white);
    fb_puts_at(24, 60, "FIRMWARE=I/O   POLICY=NATIVE AOT", fb_dim);

    for (i = 1; i < hw.n_contexts && i <= 4; i++) {
        HwContext *g = &hw.ctx[i];
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
        fb_puts_at(128, y, g->name, fb_white);
        fb_puts_at(400, y, "        ", fb_dim);
        fb_puts_at(400, y, st, colr);
        fb_puts_at(520, y, "CYC", fb_dim);
        fb_i32_at(576, y, g->cycles, fb_white);
        fb_puts_at(688, y, "FLT", fb_dim);
        fb_i32_at(744, y, g->fault, g->fault ? fb_bad : fb_dim);
    }

    hx = 24;
    hy = 250;
    barw = fb && fb->width > 80 ? (int)fb->width - 48 : 640;
    if (barw > HEAT_N * 3) {
        barw = HEAT_N * 3;
    }
    hh = 48;
    fb_puts_at(hx, hy - 20, "RUNCTX HEATMAP (TIME ->)", fb_dim);
    fb_rect(hx, hy, barw, hh, fb_idle);
    n = heat_fill;
    for (col = 0; col < n; col++) {
        int idx = (heat_i - n + col + HEAT_N) % HEAT_N;
        int cid = heat_cid[idx];
        int px = hx + (col * barw) / (n < 8 ? 8 : n);
        int pw = barw / (n < 8 ? 8 : n);
        if (pw < 1) {
            pw = 1;
        }
        fb_rect(px, hy, pw, hh, (cid >= 1 && cid <= 4) ? fb_guest[cid] : fb_dim);
    }

    fb_puts_at(24, 314, "SWITCHES", fb_dim);
    fb_i32_at(180, 314, hw.switch_count, fb_white);
    fb_puts_at(320, 314, "ISOLATION", fb_dim);
    fb_i32_at(500, 314, hw.isolation_n, hw.isolation_n ? fb_bad : fb_ok);
    fb_puts_at(24, 336, "SPAWNS", fb_dim);
    fb_i32_at(180, 336, spawn_count, fb_white);
    fb_puts_at(320, 336, "FAULTS", fb_dim);
    fb_i32_at(500, 336, fault_events, fault_events ? fb_bad : fb_white);
    fb_puts_at(24, 368, "1-4 SLOT  S F X C M P KIND  W PUT  R GET  Z PLUG  I INSPECT  Q QUIT", fb_accent);
    if (hw.has_disk && want_redraw) {
        int b;
        fb_puts_at(24, 392, "DEVICE BLOCKS", fb_dim);
        for (b = 0; b < 32 && b < hw.n_blocks; b++) {
            int32_t st = evoos_hw_blkrd(b, 1700);
            int32_t w0 = st ? 0 : evoos_hw_load(1700);
            uint32_t colr = w0 ? fb_accent : fb_idle;
            int bx = 24 + (b % 16) * 18;
            int by = 412 + (b / 16) * 18;
            fb_rect(bx, by, 14, 14, colr);
        }
    }
    want_redraw = 0;
}

static void print_help(void) {
    cons_puts("keys: 1-4 spawn  s/f/x/c/m/p kind  w put  r get  z pull-plug  i inspect  q quit\n");
    cons_puts("loader is firmware; ADDPROT/RUNCTX/BLK* are native AOT trampolines\n");
}

static void inspect(void) {
    int32_t i;
    cons_puts("inspect isolation_n=");
    cons_i32(hw.isolation_n);
    cons_puts(" switches=");
    cons_i32(hw.switch_count);
    cons_puts(" cycles=");
    cons_i32(hw.cycles);
    cons_puts(" disk=");
    cons_i32(hw.has_disk);
    cons_puts(" last_svc=");
    cons_i32(hw.last_svc);
    cons_putc('\n');
    for (i = 1; i < hw.n_contexts; i++) {
        HwContext *g = &hw.ctx[i];
        cons_puts("  ctx ");
        cons_i32(i);
        cons_puts(" ");
        cons_puts(g->name);
        cons_puts(" halt=");
        cons_i32(g->halted);
        cons_puts(" fault=");
        cons_i32(g->fault);
        cons_puts(" cyc=");
        cons_i32(g->cycles);
        cons_puts(" prot=");
        cons_i32(g->n_prot);
        cons_puts(" vec=");
        cons_i32(g->last_vector);
        cons_putc('\n');
    }
}

static int kind_eq(const char *a, const char *b) {
    while (*a && *b && *a == *b) {
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

static void loader_spawn(int slot, const char *kind) {
    const uint8_t *code;
    const uint8_t *end;
    const char *name = kind;
    int32_t init_off[16];
    int32_t init_val[16];
    int n_init = 0;
    int i;
    if (slot < 0 || slot > 3) {
        return;
    }
    if (kind_eq(kind, "sum")) {
        code = guest_sum;
        end = guest_sum_end;
        name = "sum_8";
        init_off[0] = 0;
        init_val[0] = 8;
        n_init = 1;
    } else if (kind_eq(kind, "fill")) {
        code = guest_fill;
        end = guest_fill_end;
        name = "fill_5";
        init_off[0] = 0;
        init_val[0] = 5;
        n_init = 1;
    } else if (kind_eq(kind, "xor")) {
        static const int32_t xv[] = {3, 5, 7, 9, 1};
        code = guest_xor;
        end = guest_xor_end;
        name = "xor_5";
        init_off[0] = 0;
        init_val[0] = 5;
        n_init = 1;
        for (i = 0; i < 5; i++) {
            init_off[n_init] = 10 + i;
            init_val[n_init] = xv[i];
            n_init++;
        }
    } else if (kind_eq(kind, "copyinc")) {
        static const int32_t cv[] = {4, 8, 2, 6};
        code = guest_copyinc;
        end = guest_copyinc_end;
        name = "copyinc_4";
        init_off[0] = 0;
        init_val[0] = 4;
        n_init = 1;
        for (i = 0; i < 4; i++) {
            init_off[n_init] = 10 + i;
            init_val[n_init] = cv[i];
            n_init++;
        }
    } else if (kind_eq(kind, "max")) {
        static const int32_t mv[] = {4, 17, 2, 9, 9};
        code = guest_max;
        end = guest_max_end;
        name = "max_5";
        init_off[0] = 0;
        init_val[0] = 5;
        n_init = 1;
        for (i = 0; i < 5; i++) {
            init_off[n_init] = 10 + i;
            init_val[n_init] = mv[i];
            n_init++;
        }
    } else if (kind_eq(kind, "malicious")) {
        code = guest_malicious;
        end = guest_malicious_end;
        name = "malicious_store";
    } else if (kind_eq(kind, "spin")) {
        code = guest_spin;
        end = guest_spin_end;
        name = "spin";
    } else if (kind_eq(kind, "put")) {
        code = guest_put;
        end = guest_put_end;
        name = "put";
        init_off[0] = 0;
        init_val[0] = 7;
        init_off[1] = 1;
        init_val[1] = 1;
        init_off[2] = 2;
        init_val[2] = 123;
        n_init = 3;
    } else if (kind_eq(kind, "get")) {
        code = guest_get;
        end = guest_get_end;
        name = "get";
        init_off[0] = 0;
        init_val[0] = 7;
        init_off[1] = 1;
        init_val[1] = 1;
        init_off[2] = 2;
        init_val[2] = 0;
        n_init = 3;
    } else {
        cons_puts("loader: unknown kind\n");
        return;
    }
    if (evoos_hw_install_guest(slot, code, (uint64_t)(end - code), name, init_off, init_val, n_init) != 0) {
        cons_puts("loader: install failed\n");
        return;
    }
    seen_halt[slot + 1] = 0;
    spawn_count++;
    io_poke(3, spawn_count);
    io_poke(4, slot);
    cons_puts("loader: slot ");
    cons_i32(slot);
    cons_puts(" ctx ");
    cons_i32(slot + 1);
    cons_puts(" ");
    cons_puts(kind);
    cons_puts(" ");
    cons_puts(name);
    cons_putc('\n');
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
    case 0x11:
        return 'w';
    case 0x13:
        return 'r';
    case 0x2c:
        return 'z';
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
    case 0x19:
        return 'p';
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
    io_poke(0, (int32_t)(uint8_t)ch);
    io_poke(1, key_seq);
    io_poke(2, scan);
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
    case 'm':
    case 'p': {
        const char *kind = "sum";
        if (ch == 'f') {
            kind = "fill";
        } else if (ch == 'x') {
            kind = "xor";
        } else if (ch == 'c') {
            kind = "copyinc";
        } else if (ch == 'm') {
            kind = "malicious";
        } else if (ch == 'p') {
            kind = "spin";
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
    case 'w':
        slot = next_slot % 4;
        next_slot++;
        loader_spawn(slot, "put");
        break;
    case 'r':
        slot = next_slot % 4;
        next_slot++;
        loader_spawn(slot, "get");
        break;
    case 'z':
        cons_puts("pull the plug (reset)\n");
        outb(0xCF9, 0x06);
        break;
    case 'i':
        inspect();
        break;
    case 'h':
        print_help();
        break;
    case 'q':
        cons_puts("EvoOS quit isolation_n=");
        cons_i32(hw.isolation_n);
        cons_puts(" spawns=");
        cons_i32(spawn_count);
        cons_putc('\n');
        qemu_exit(hw.isolation_n == 0 ? 0 : 1);
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

void evoos_firmware_after_runctx(int32_t cid, int32_t ran, int32_t fault, int32_t vector) {
    static int in_persist;
    note_runctx(cid, ran, fault, vector);
    /* Dispatch only. PUT/GET policy is the evolved persist blob. */
    if ((hw.last_svc == 1 || hw.last_svc == 2) && !in_persist) {
        in_persist = 1;
        persist_storage_main();
        in_persist = 0;
    }
    poll_ps2();
    poll_serial();
    if (want_redraw || (hw.switch_count & 15) == 0) {
        dashboard();
    }
}

void kmain(void) {
    uint64_t hhdm = 0, kphys = 0, kvirt = 0;
    static volatile uint32_t keep_data = 0x45564F53;
    int32_t z = 0;

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

    if (hhdm_request.response) {
        hhdm = hhdm_request.response->offset;
    }
    if (kernel_address_request.response) {
        kphys = kernel_address_request.response->physical_base;
        kvirt = kernel_address_request.response->virtual_base;
    }
    evoos_hw_init(hhdm, kphys, kvirt);

    cons_puts("EvoOS live\n");
    cons_puts("EvoOS hw ring0-aot\n");
    cons_puts("firmware is not a kernel; supervisor=");
    cons_puts(supervisor_origin);
    cons_putc('\n');
    cons_puts("storage=");
    cons_puts(persist_storage_origin);
    cons_putc('\n');
    if (hw.has_disk) {
        cons_puts("device attached blocks=");
        cons_i32(hw.n_blocks);
        cons_putc('\n');
    } else {
        cons_puts("device absent\n");
    }
    print_help();

    loader_spawn(0, "sum");
    loader_spawn(1, "fill");
    loader_spawn(2, "xor");
    loader_spawn(3, "spin");

    if (fb) {
        fb_fill(fb_bg);
    }
    dashboard();
    drawn = 1;

    /* Policy: the AOT supervisor. Firmware never chooses a guest. */
    supervisor_main();

    cons_puts("supervisor returned isolation_n=");
    cons_i32(hw.isolation_n);
    cons_puts(" switches=");
    cons_i32(hw.switch_count);
    cons_putc('\n');
    for (;;) {
        poll_ps2();
        poll_serial();
        dashboard();
        (void)z;
        __asm__ volatile("hlt");
    }
}
