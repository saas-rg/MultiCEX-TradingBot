from decimal import Decimal

def dquant(x: Decimal, prec: int) -> Decimal:
    if prec <= 0:
        q = Decimal(1)
    else:
        q = Decimal(1).scaleb(-prec)
    return (x // q) * q

def fmt(x: Decimal, prec: int) -> str:
    q = dquant(x, prec)
    return f"{q:f}"
