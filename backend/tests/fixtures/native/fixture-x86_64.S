    .text

    .global CurrencyManager_CanAfford
    .type CurrencyManager_CanAfford,@function
CurrencyManager_CanAfford:
    mov $0, %eax
    ret
    .size CurrencyManager_CanAfford, .-CurrencyManager_CanAfford

    .global NoirNopTarget
    .type NoirNopTarget,@function
NoirNopTarget:
    nop
    nop
    nop
    nop
    ret
    .size NoirNopTarget, .-NoirNopTarget

    .global NoirBranch
    .type NoirBranch,@function
NoirBranch:
    jmp NoirTarget
    .size NoirBranch, .-NoirBranch

    .global NoirTarget
    .type NoirTarget,@function
NoirTarget:
    mov $7, %eax
    ret
    .size NoirTarget, .-NoirTarget

    .section .note.GNU-stack,"",@progbits
