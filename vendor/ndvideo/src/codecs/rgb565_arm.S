/* Exact YUV420 -> RGB565 for a pair of dense rows (ARM926, little endian).
 * r0/r1: Y rows; r2/r3: U/V. Stack: dst0, dst1, y_base, pair count.
 * y_base is followed by four 256-entry chroma tables, then the clip table.
 * Caller supplies nonempty even width and word-aligned destination rows. */
.syntax unified
.arm
.section .text.yuv420_rgb565_pair_rows,"ax",%progbits

/* One chroma sample supplies four pixels in either row loop. */
.macro convert_dense_block
    ldrb r12, [r2], #1
    add r11, r6, r12, lsl #2
    ldr r10, [r11, #1024]
    ldr r9, [r11, #2048]
    ldrb r12, [r3], #1
    add r11, r6, r12, lsl #2
    add r11, r11, #4096
    ldr r8, [r11, #-1024]
    ldr r12, [r11]
    add r9, r9, r12
    ldrb r12, [r0], #1
    ldr r11, [r6, r12, lsl #2]
    add r12, r11, r10
    ldrb lr, [r7, r12, asr #11]
    add r12, r11, r9
    ldrb r12, [r7, r12, asr #10]
    orr lr, lr, r12, lsl #5
    add r12, r11, r8
    ldrb r12, [r7, r12, asr #11]
    orr lr, lr, r12, lsl #11
    ldrb r12, [r0], #1
    ldr r11, [r6, r12, lsl #2]
    add r12, r11, r10
    ldrb r12, [r7, r12, asr #11]
    orr lr, lr, r12, lsl #16
    add r12, r11, r9
    ldrb r12, [r7, r12, asr #10]
    orr lr, lr, r12, lsl #21
    add r12, r11, r8
    ldrb r12, [r7, r12, asr #11]
    orr lr, lr, r12, lsl #27
    str lr, [r4], #4
    ldrb r12, [r1], #1
    ldr r11, [r6, r12, lsl #2]
    add r12, r11, r10
    ldrb lr, [r7, r12, asr #11]
    add r12, r11, r9
    ldrb r12, [r7, r12, asr #10]
    orr lr, lr, r12, lsl #5
    add r12, r11, r8
    ldrb r12, [r7, r12, asr #11]
    orr lr, lr, r12, lsl #11
    ldrb r12, [r1], #1
    ldr r11, [r6, r12, lsl #2]
    add r12, r11, r10
    ldrb r12, [r7, r12, asr #11]
    orr lr, lr, r12, lsl #16
    add r12, r11, r9
    ldrb r12, [r7, r12, asr #10]
    orr lr, lr, r12, lsl #21
    add r12, r11, r8
    ldrb r12, [r7, r12, asr #11]
    orr lr, lr, r12, lsl #27
    str lr, [r5], #4
.endm

.align 5
.global yuv420_rgb565_pair_rows
.type yuv420_rgb565_pair_rows, %function
yuv420_rgb565_pair_rows:
    push {r4-r11,lr}
    ldr r4,[sp,#36]
    ldr r5,[sp,#40]
    ldr r6,[sp,#44]
    ldr r8,[sp,#48]
    cmp r8,#0
    popeq {r4-r11,pc}
    add r7,r6,#5120
    add r11,r0,r8,lsl #1
    push {r11}
    ands r8,r8,#3
    beq 1f
    cmp r8,#1
    beq 4f
    cmp r8,#2
    beq 3f
    b 2f
1:
    convert_dense_block
2:
    convert_dense_block
3:
    convert_dense_block
4:
    convert_dense_block
    ldr r12,[sp]
    cmp r0,r12
    blo 1b
    add sp,sp,#4
    pop {r4-r11,pc}
.size yuv420_rgb565_pair_rows, .-yuv420_rgb565_pair_rows
/* Exact flat/repeated YUV420 blocks, with the same eight arguments as
 * yuv420_rgb565_pair_rows. Last-key and last-pixel live only for these rows. */
.section .text.yuv420_rgb565_flat_pair_rows,"ax",%progbits
.align 5
.global yuv420_rgb565_flat_pair_rows
.type yuv420_rgb565_flat_pair_rows,%function
.macro convert_flat_block
    ldrb r12,[r0]
    ldrb r11,[r0,#1]
    cmp r12,r11
    ldrbeq r11,[r1]
    cmpeq r12,r11
    ldrbeq r11,[r1,#1]
    cmpeq r12,r11
    bne 12f
    ldrb r11,[r2],#1
    orr r12,r12,r11,lsl #8
    ldrb lr,[r3],#1
    orr r12,r12,lr,lsl #16
    ldr r11,[sp,#4]
    cmp r12,r11
    ldreq lr,[sp,#8]
    beq 11f
    str r12,[sp,#4]
    and r11,r12,#255
    ldr r11,[r6,r11,lsl #2]
    mov r12,r12,lsr #8
    and r12,r12,#255
    add r8,r6,r12,lsl #2
    ldr r10,[r8,#1024]
    ldr r9,[r8,#2048]
    add r8,r6,lr,lsl #2
    add r8,r8,#4096
    ldr r12,[r8]
    add r9,r9,r12
    ldr r8,[r8,#-1024]
    add r12,r11,r10
    ldrb lr,[r7,r12,asr #11]
    add r12,r11,r9
    ldrb r12,[r7,r12,asr #10]
    orr lr,lr,r12,lsl #5
    add r12,r11,r8
    ldrb r12,[r7,r12,asr #11]
    orr lr,lr,r12,lsl #11
    orr lr,lr,lr,lsl #16
    str lr,[sp,#8]
11:
    str lr,[r4],#4
    str lr,[r5],#4
    add r0,r0,#2
    add r1,r1,#2
    b 13f
12:
    convert_dense_block
13:
.endm
yuv420_rgb565_flat_pair_rows:
    push {r4-r11,lr}
    ldr r4,[sp,#36]
    ldr r5,[sp,#40]
    ldr r6,[sp,#44]
    ldr r8,[sp,#48]
    cmp r8,#0
    popeq {r4-r11,pc}
    add r7,r6,#5120
    add r11,r0,r8,lsl #1
    sub sp,sp,#12
    str r11,[sp]
    mvn r12,#0
    str r12,[sp,#4]
    ands r8,r8,#3
    beq 20f
    cmp r8,#1
    beq 23f
    cmp r8,#2
    beq 22f
    b 21f
20:
    convert_flat_block
21:
    convert_flat_block
22:
    convert_flat_block
23:
    convert_flat_block
    ldr r12,[sp]
    cmp r0,r12
    blo 20b
    add sp,sp,#12
    pop {r4-r11,pc}
.size yuv420_rgb565_flat_pair_rows,.-yuv420_rgb565_flat_pair_rows
.section .note.GNU-stack,"",%progbits
