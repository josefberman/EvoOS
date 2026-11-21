    .section .note.GNU-stack,"",@progbits
    .section .rodata
    .global evoos_image
    .global evoos_image_end
    .align 4
evoos_image:
    .incbin "boot.img"
evoos_image_end:
