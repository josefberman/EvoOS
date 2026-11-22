    .section .rodata
    .code64
    .align 16

/* User guests are PIC; firmware copies them to HW_CODE_VA. RDI = data window. */

    .global guest_sum, guest_sum_end
guest_sum:
    movl (%rdi), %ecx
    xorl %eax, %eax
    movl $1, %edx
1:
    cmpl %edx, %ecx
    jl 2f
    addl %edx, %eax
    incl %edx
    jmp 1b
2:
    movl %eax, 8(%rdi)
    int $0x80
guest_sum_end:

    .global guest_fill, guest_fill_end
guest_fill:
    movl (%rdi), %ecx
    xorl %edx, %edx
1:
    cmpl %ecx, %edx
    jge 2f
    movl %edx, %eax
    incl %eax
    movl %edx, %r8d
    addl $10, %r8d
    movl %eax, (%rdi, %r8, 4)
    incl %edx
    jmp 1b
2:
    int $0x80
guest_fill_end:

    .global guest_xor, guest_xor_end
guest_xor:
    movl (%rdi), %ecx
    xorl %eax, %eax
    xorl %edx, %edx
1:
    cmpl %ecx, %edx
    jge 2f
    movl %edx, %r8d
    addl $10, %r8d
    movl (%rdi, %r8, 4), %r9d
    xorl %r9d, %eax
    incl %edx
    jmp 1b
2:
    movl %eax, 8(%rdi)
    int $0x80
guest_xor_end:

    .global guest_copyinc, guest_copyinc_end
guest_copyinc:
    movl (%rdi), %ecx
    xorl %edx, %edx
1:
    cmpl %ecx, %edx
    jge 2f
    movl %edx, %r8d
    addl $10, %r8d
    movl (%rdi, %r8, 4), %eax
    incl %eax
    movl %r8d, %r9d
    addl %ecx, %r9d
    movl %eax, (%rdi, %r9, 4)
    incl %edx
    jmp 1b
2:
    int $0x80
guest_copyinc_end:

    .global guest_max, guest_max_end
guest_max:
    movl (%rdi), %ecx
    xorl %eax, %eax
    xorl %edx, %edx
1:
    cmpl %ecx, %edx
    jge 2f
    movl %edx, %r8d
    addl $10, %r8d
    movl (%rdi, %r8, 4), %r9d
    cmpl %r9d, %eax
    jge 3f
    movl %r9d, %eax
3:
    incl %edx
    jmp 1b
2:
    movl %eax, 8(%rdi)
    int $0x80
guest_max_end:

    /* RSI = victim window VA (another slot). Load there → #PF if unmapped. */
    .global guest_malicious, guest_malicious_end
guest_malicious:
    movl 8(%rsi), %eax
    int $0x80
guest_malicious_end:

    /* Third-guest STORE of the pair window. Must #PF unless the window leaked. */
    .global guest_malicious_store, guest_malicious_store_end
guest_malicious_store:
    movl $99, 8(%rsi)
    int $0x80
guest_malicious_store_end:

    .global guest_spin, guest_spin_end
guest_spin:
1:
    jmp 1b
guest_spin_end:

    /* rax=1 PUT / rax=2 GET then rax=0 halt. Window: [0]=key [1]=n [2]=payload. */
    .global guest_put, guest_put_end
guest_put:
    movl $1, %eax
    int $0x80
    xorl %eax, %eax
    int $0x80
guest_put_end:

    .global guest_get, guest_get_end
guest_get:
    movl $2, %eax
    int $0x80
    xorl %eax, %eax
    int $0x80
guest_get_end:

    /* Store across the data window, then loop. Memory-pressure workload. */
    .global guest_pressure, guest_pressure_end
guest_pressure:
    xorl %edx, %edx
1:
    cmpl $120, %edx
    jge 2f
    movl %edx, (%rdi, %rdx, 4)
    incl %edx
    jmp 1b
2:
    xorl %edx, %edx
    jmp 1b
guest_pressure_end:

    /* SVC 3 route: window[0]=dest ctx, window[2]=payload. */
    .global guest_route, guest_route_end
guest_route:
    movl $3, %eax
    int $0x80
    xorl %eax, %eax
    int $0x80
guest_route_end:

    /* Producer: write 77 at offset 2 of own window (RDI). */
    .global guest_route_prod, guest_route_prod_end
guest_route_prod:
    movl $77, 8(%rdi)
    int $0x80
guest_route_prod_end:

    /* Consumer: read offset 2 of peer window (RSI). Needs shared-window grant. */
    .global guest_route_cons, guest_route_cons_end
guest_route_cons:
    movl 8(%rsi), %eax
    int $0x80
guest_route_cons_end:
