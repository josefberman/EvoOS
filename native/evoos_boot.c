#include "evoos_vm.h"
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
static uint32_t fb_white, fb_bg, fb_accent, fb_ok, fb_bad, fb_idle;
static int fb_row, fb_col;

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

static void fb_glyph(int x, int y, char ch, uint32_t color) {
    int row, col, idx;
    const uint8_t *g;
    if (ch >= 'a' && ch <= 'z') {
        ch = (char)(ch - 32);
    }
    if (ch < 32 || ch > 90) {
        ch = '?';
    }
    idx = ch - 32;
    g = font8[idx];
    for (row = 0; row < 8; row++) {
        uint8_t bits = g[row];
        for (col = 0; col < 8; col++) {
            if (bits & (0x80 >> col)) {
                fb_rect(x + col * 2, y + row * 2, 2, 2, color);
            }
        }
    }
}

static void cons_putc(char c) {
    if (c == '\n') {
        serial_putc('\r');
        serial_putc('\n');
        fb_col = 16;
        fb_row += 20;
        return;
    }
    serial_putc(c);
    if (c == '\r') {
        return;
    }
    if (fb) {
        fb_glyph(fb_col, fb_row, c, fb_white);
        fb_col += 18;
        if (fb_col > (int)fb->width - 24) {
            fb_col = 16;
            fb_row += 20;
        }
    }
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

static void io_store(int32_t addr, int32_t val) {
    (void)addr;
    cons_putc((char)(val & 0xFF));
}

static int32_t seen_halt[EVOOS_MAX_CTX];
static EvoOSMachine machine;

static void on_runctx(const EvoOSMachine *m, int32_t cid, int32_t ran) {
    const EvoOSContext *g;
    if (cid <= 0 || cid >= m->n_contexts) {
        return;
    }
    g = &m->contexts[cid];
    if (g->halted && !seen_halt[cid]) {
        seen_halt[cid] = 1;
        cons_puts("  guest ");
        cons_i32(cid);
        cons_puts(" ");
        if (cid - 1 < m->n_guests) {
            cons_puts(m->guest_name[cid - 1]);
        }
        cons_puts(" halted cycles=");
        cons_i32(g->cycles);
        cons_puts(" ran=");
        cons_i32(ran);
        cons_putc('\n');
        if (fb) {
            int gx = 40 + (cid - 1) * 90;
            fb_rect(gx, 90, 70, 40, g->fault ? fb_bad : fb_ok);
        }
    }
}

static void draw_banner(void) {
    if (!fb) {
        return;
    }
    fb_fill(fb_bg);
    /* block letters E V O O S */
    fb_rect(40, 20, 12, 48, fb_accent);
    fb_rect(40, 20, 36, 12, fb_accent);
    fb_rect(40, 38, 28, 10, fb_accent);
    fb_rect(40, 56, 36, 12, fb_accent);
    fb_rect(90, 20, 12, 48, fb_accent);
    fb_rect(126, 20, 12, 48, fb_accent);
    fb_rect(102, 36, 12, 16, fb_accent);
    fb_rect(150, 20, 40, 12, fb_accent);
    fb_rect(150, 20, 12, 48, fb_accent);
    fb_rect(178, 20, 12, 48, fb_accent);
    fb_rect(150, 56, 40, 12, fb_accent);
    fb_rect(202, 20, 40, 12, fb_accent);
    fb_rect(202, 20, 12, 48, fb_accent);
    fb_rect(230, 20, 12, 48, fb_accent);
    fb_rect(202, 56, 40, 12, fb_accent);
    fb_rect(254, 20, 40, 12, fb_accent);
    fb_rect(254, 20, 12, 48, fb_accent);
    fb_rect(254, 38, 32, 10, fb_accent);
    fb_rect(254, 56, 40, 12, fb_accent);
    fb_row = 140;
    fb_col = 16;
}

void kmain(void) {
    size_t img_len;
    int rc;
    int32_t i, completed;
    uint64_t hhdm = 0;
    static volatile uint32_t keep_data = 0x45564F53; /* force a .data PT_LOAD */

    (void)keep_data;
    serial_init();
    /* Serial first. Do not poke HHDM+0xB8000: that MMIO is often unmapped and
     * triple-faults. Limine's framebuffer is a mapped GOP/VESA window. */
    cons_puts("EvoOS firmware starting\n");
    (void)hhdm;
    if (hhdm_request.response) {
        hhdm = hhdm_request.response->offset;
        (void)hhdm;
    }

    if (framebuffer_request.response && framebuffer_request.response->framebuffer_count > 0) {
        fb = framebuffer_request.response->framebuffers[0];
        if (fb->bpp != 32 || fb->memory_model != LIMINE_FRAMEBUFFER_RGB) {
            fb = 0;
        } else {
            fb_bg = fb_pixel(fb, 8, 10, 24);
            fb_white = fb_pixel(fb, 230, 230, 240);
            fb_accent = fb_pixel(fb, 80, 200, 255);
            fb_ok = fb_pixel(fb, 40, 200, 90);
            fb_bad = fb_pixel(fb, 220, 60, 60);
            fb_idle = fb_pixel(fb, 60, 70, 90);
            draw_banner();
            for (i = 0; i < 4; i++) {
                fb_rect(40 + i * 90, 90, 70, 40, fb_idle);
            }
        }
    }

    cons_puts("EvoOS firmware (not a kernel)\n");
    cons_puts("ISA: 5-word instr op,a,b,c,imm\n");

    img_len = (size_t)(evoos_image_end - evoos_image);
    rc = evoos_load_image(&machine, evoos_image, img_len);
    if (rc != 0) {
        cons_puts("image load failed: ");
        cons_i32(rc);
        cons_putc('\n');
        qemu_exit(1);
    }

    cons_puts("supervisor: ");
    cons_puts(machine.origin);
    cons_puts("  workload: ");
    cons_puts(machine.workload);
    cons_putc('\n');
    cons_puts("contexts=");
    cons_i32(machine.n_contexts);
    cons_puts(" mem=");
    cons_i32(machine.mem_words);
    cons_puts(" max_steps=");
    cons_i32(machine.max_steps);
    cons_putc('\n');
    cons_puts("running supervisor...\n");

    evoos_store_hook = io_store;
    evoos_runctx_hook = on_runctx;
    evoos_run(&machine, -1);

    cons_puts("[halt]\n");
    completed = 0;
    for (i = 1; i < machine.n_contexts; i++) {
        EvoOSContext *g = &machine.contexts[i];
        int32_t ok = 0;
        int32_t e;
        int32_t n_for = 0;
        int32_t n_hit = 0;
        for (e = 0; e < machine.n_expected; e++) {
            if (machine.exp_cid[e] != i) {
                continue;
            }
            n_for++;
            if (machine.exp_addr[e] >= 0 && machine.exp_addr[e] < machine.mem_words &&
                machine.mem[machine.exp_addr[e]] == machine.exp_val[e]) {
                n_hit++;
            }
        }
        ok = g->halted && g->fault == 0 && n_for > 0 && n_hit == n_for;
        if (ok) {
            completed++;
        }
        cons_puts("  guest ");
        cons_i32(i);
        cons_puts(" ");
        if (i - 1 < machine.n_guests) {
            cons_puts(machine.guest_name[i - 1]);
        }
        cons_puts(" halted=");
        cons_i32(g->halted);
        cons_puts(" fault=");
        cons_i32(g->fault);
        cons_puts(" cycles=");
        cons_i32(g->cycles);
        cons_puts(ok ? " expected=OK\n" : " expected=FAIL\n");
        if (fb) {
            fb_rect(40 + (i - 1) * 90, 90, 70, 40, ok ? fb_ok : (g->fault ? fb_bad : fb_idle));
        }
    }

    cons_puts("completed ");
    cons_i32(completed);
    cons_puts("/");
    cons_i32(machine.n_guests);
    cons_puts(" isolation=");
    cons_puts(machine.isolation_n == 0 ? "ok" : "HIT");
    cons_puts(" switches=");
    cons_i32(machine.switch_count);
    cons_puts(" cycles=");
    cons_i32(machine.cycles);
    cons_putc('\n');
    cons_puts("supervisor_fault=");
    cons_i32(machine.supervisor_fault);
    cons_puts(" halted=");
    cons_i32(machine.halted);
    cons_putc('\n');
    cons_puts("EvoOS done.\n");

    if (completed == machine.n_guests && machine.isolation_n == 0) {
        qemu_exit(0);
    }
    qemu_exit(1);
}
