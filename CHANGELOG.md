## v0.8.0 — HTX connection (M1–M7)
- Multi-exchange registry (gate, htx) + lazy imports.
- Pairs config with exchange binding; web admin supports exchange select.
- Engine routes ops through exchange proxy; per-exchange cancel_all at start/pause/new BUY.
- Reporting: exchange column in CSV; NET per (exchange+pair); JSON summary endpoint.
- HTX Spot adapter (MVP): prices/limits/orders/balances/trades.
- Verified concurrent run: Gate testnet + HTX spot.
