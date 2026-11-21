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

    /* RSI = victim window VA (another slot). Store there → #PF if unmapped. */
    .global guest_malicious, guest_malicious_end
guest_malicious:
    movl $12345, 8(%rsi)
    int $0x80
guest_malicious_end:

    .global guest_spin, guest_spin_end
guest_spin:
1:
    jmp 1b
guest_spin_end:
