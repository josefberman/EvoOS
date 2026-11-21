#include "evoos_vm.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static FILE *g_out;

static void emit(const char *line) {
    fputs(line, g_out);
    fputc('\n', g_out);
}

static uint8_t *read_file(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    uint8_t *buf;
    long sz;
    if (!f) {
        return 0;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return 0;
    }
    sz = ftell(f);
    if (sz < 0) {
        fclose(f);
        return 0;
    }
    rewind(f);
    buf = (uint8_t *)malloc((size_t)sz);
    if (!buf) {
        fclose(f);
        return 0;
    }
    if (fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
        free(buf);
        fclose(f);
        return 0;
    }
    fclose(f);
    *n = (size_t)sz;
    return buf;
}

int main(int argc, char **argv) {
    const char *image = 0;
    const char *trace_path = 0;
    int32_t steps = -1;
    int i;
    size_t n = 0;
    uint8_t *blob;
    EvoOSMachine m;
    int rc;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--trace") && i + 1 < argc) {
            trace_path = argv[++i];
        } else if (!strcmp(argv[i], "--steps") && i + 1 < argc) {
            steps = (int32_t)atoi(argv[++i]);
        } else if (argv[i][0] != '-') {
            image = argv[i];
        } else {
            fprintf(stderr, "usage: evoos_host [--trace FILE] [--steps N] IMAGE\n");
            return 2;
        }
    }
    if (!image) {
        fprintf(stderr, "usage: evoos_host [--trace FILE] [--steps N] IMAGE\n");
        return 2;
    }
    blob = read_file(image, &n);
    if (!blob) {
        fprintf(stderr, "cannot read %s\n", image);
        return 1;
    }
    rc = evoos_load_image(&m, blob, n);
    free(blob);
    if (rc != 0) {
        fprintf(stderr, "load_image failed: %d\n", rc);
        return 1;
    }
    evoos_run(&m, steps);
    if (trace_path) {
        g_out = fopen(trace_path, "w");
        if (!g_out) {
            fprintf(stderr, "cannot write %s\n", trace_path);
            return 1;
        }
    } else {
        g_out = stdout;
    }
    evoos_dump_trace(&m, emit);
    if (trace_path) {
        fclose(g_out);
    }
    return 0;
}
