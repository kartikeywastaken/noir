    .text

    .global CurrencyManager_CanAfford
    .type CurrencyManager_CanAfford, %function
CurrencyManager_CanAfford:
    mov w0, #0
    ret
    .size CurrencyManager_CanAfford, .-CurrencyManager_CanAfford

    .global NoirNopTarget
    .type NoirNopTarget, %function
NoirNopTarget:
    nop
    nop
    nop
    ret
    .size NoirNopTarget, .-NoirNopTarget

    .global NoirBranch
    .type NoirBranch, %function
NoirBranch:
    b NoirTarget
    .size NoirBranch, .-NoirBranch

    .global NoirTarget
    .type NoirTarget, %function
NoirTarget:
    mov w0, #7
    ret
    .size NoirTarget, .-NoirTarget

    .section .note.GNU-stack,"",%progbits
