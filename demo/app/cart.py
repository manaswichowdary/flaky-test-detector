DEFAULT_DISCOUNTS: dict[str, float] = {}


class Cart:
    # Bug planted for the shared-state demo: every Cart shares one discounts dict.
    def __init__(self, discounts: dict[str, float] = DEFAULT_DISCOUNTS):
        self.items: list[tuple[str, float, int]] = []
        self.discounts = discounts

    def add(self, sku: str, price: float, qty: int = 1) -> None:
        if qty <= 0:
            raise ValueError("qty must be positive")
        self.items.append((sku, price, qty))

    def add_discount(self, sku: str, pct: float) -> None:
        self.discounts[sku] = pct

    def total(self) -> float:
        total = 0.0
        for sku, price, qty in self.items:
            total += price * qty * (1 - self.discounts.get(sku, 0.0))
        return round(total, 2)
