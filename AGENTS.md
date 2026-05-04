# TradeOS V3 Agent Rules

1. Safety beats profit.
2. Broker state is the source of truth.
3. Long-only strategies must never create shorts.
4. Fills are idempotent by broker order_id.
5. Strategies generate signals only; they never submit orders.
6. Risk manager is the final gate before execution.
7. No live trading behavior without tests.
8. Prefer small modules and explicit interfaces.
9. If state is dirty, block execution.
10. Every change must include or update tests.
