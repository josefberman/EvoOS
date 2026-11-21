    .section .text
    .code64
    .extern evoos_hw_trap
    .extern hw_kregs
    .global hw_runctx_resume
    .global isr_common

    .macro ISR_NOERR vec
    .p2align 4
    .global isr_\vec
isr_\vec:
    pushq $0
    pushq $\vec
    jmp isr_common
    .endm

    .macro ISR_ERR vec
    .p2align 4
    .global isr_\vec
isr_\vec:
    pushq $\vec
    jmp isr_common
    .endm

    ISR_NOERR 0
    ISR_NOERR 1
    ISR_NOERR 2
    ISR_NOERR 3
    ISR_NOERR 4
    ISR_NOERR 5
    ISR_NOERR 6
    ISR_NOERR 7
    ISR_ERR 8
    ISR_NOERR 9
    ISR_ERR 10
    ISR_ERR 11
    ISR_ERR 12
    ISR_ERR 13
    ISR_ERR 14
    ISR_NOERR 15
    ISR_NOERR 16
    ISR_ERR 17
    ISR_NOERR 18
    ISR_NOERR 19
    ISR_NOERR 20
    ISR_NOERR 21
    ISR_NOERR 22
    ISR_NOERR 23
    ISR_NOERR 24
    ISR_NOERR 25
    ISR_NOERR 26
    ISR_NOERR 27
    ISR_NOERR 28
    ISR_NOERR 29
    ISR_ERR 30
    ISR_NOERR 31
    ISR_NOERR 32
    ISR_NOERR 128

isr_common:
    pushq %r15
    pushq %r14
    pushq %r13
    pushq %r12
    pushq %r11
    pushq %r10
    pushq %r9
    pushq %r8
    pushq %rdi
    pushq %rsi
    pushq %rbp
    pushq %rbx
    pushq %rdx
    pushq %rcx
    pushq %rax
    movq %rsp, %rdi
    call evoos_hw_trap
    /* C never returns for a user trap; kernel faults hang inside. */

hw_runctx_resume:
    movq hw_kregs+0(%rip), %rbx
    movq hw_kregs+8(%rip), %rbp
    movq hw_kregs+16(%rip), %r12
    movq hw_kregs+24(%rip), %r13
    movq hw_kregs+32(%rip), %r14
    movq hw_kregs+40(%rip), %r15
    movq hw_kregs+48(%rip), %rsp
    movl hw_ran(%rip), %eax
    ret

    .global evoos_hw_runctx
evoos_hw_runctx:
    /* SysV: edi=cid, esi=n_steps. Save callee-saved + rsp, then enter. */
    movq %rbx, hw_kregs+0(%rip)
    movq %rbp, hw_kregs+8(%rip)
    movq %r12, hw_kregs+16(%rip)
    movq %r13, hw_kregs+24(%rip)
    movq %r14, hw_kregs+32(%rip)
    movq %r15, hw_kregs+40(%rip)
    movq %rsp, hw_kregs+48(%rip)
    call evoos_hw_runctx_enter
    /* If enter returns, guest was a nop (halted/fault/invalid). */
    movl hw_ran(%rip), %eax
    ret

    .global evoos_hw_iretq
evoos_hw_iretq:
    /* rdi=&frame: rip,cs,rflags,rsp,ss then we load GPRs from a side struct.
       Simpler: C pushes the iret frame and jumps here with rdi=guest rdi, rsi=guest rsi. */
    iretq

    .bss
    .align 8
    .global hw_kregs
    .global hw_ran
hw_kregs:
    .zero 56
hw_ran:
    .zero 4
