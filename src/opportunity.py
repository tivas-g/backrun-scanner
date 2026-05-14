"""
Backrun opportunity detection.

Mental model
------------
A victim tx swaps token A → token B on one v2 pool (the "victim pool"), making
A abundant and B scarce there.  The same pair trades on at least one other venue
(the "counterparty pool") whose reserves haven't moved.

We exploit the displacement by:
  Leg 1 — sell B into the victim pool's now-thin B side, buying cheap A.
  Leg 2 — sell that A on the counterparty pool at its undisturbed price, receiving B.

Net result: we start and end in B, pocketing the spread minus two 0.3 % fees.

Because we're completing a round-trip in B (the victim's output token), the
backrun input and profit are both denominated in B.

Scope
-----
Only the three exact-input router variants (swapExactTokensForTokens,
swapExactETHForTokens, swapExactTokensForETH) are detected — the decoder
filters everything else upstream.  Exact-output variants would require
simulating the router's actual input through the path, which is out of scope.
"""

from __future__ import annotations

from src.amm import optimal_two_pool_arb, simulate_swap
from src.pools import Pool, PoolRegistry


def detect_opportunity(
    decoded_swap: dict,
    registry: PoolRegistry,
    amount_in_override: int | None = None,
) -> dict | None:
    """
    Return an opportunity dict if the decoded pending swap creates a profitable
    backrun, otherwise return None.

    decoded_swap       — output of src.decoder.decode_swap()
    registry           — PoolRegistry with current reserves already cached
    amount_in_override — for swapExactETHForTokens the caller must pass
                         tx['value'] here; ignored for the other two variants
    """
    # ── 1. Reject multi-hop swaps ─────────────────────────────────────────────
    path: list[str] = decoded_swap.get("path", [])
    if len(path) != 2:
        return None

    # ── 2. Resolve effective amount_in ────────────────────────────────────────
    amount_in: int | None = decoded_swap.get("amount_in")
    if amount_in is None:
        amount_in = amount_in_override
    if amount_in is None:
        return None

    # ── 3. Locate the victim pool ─────────────────────────────────────────────
    victim_pool: Pool | None = registry.find_pool_by_router_path(path)
    if victim_pool is None:
        return None

    # Guard against a registry that hasn't been refreshed yet.
    if victim_pool.reserve0 == 0 or victim_pool.reserve1 == 0:
        return None

    # ── 4. Determine swap direction ───────────────────────────────────────────
    # Naming: "victim_direction" describes what the VICTIM does, not us.
    # Our backrun runs the opposite direction on the victim pool.
    # The explicit elif + else makes a malformed path return None instead of
    # silently misorienting the reserves.
    token_in_addr = path[0].lower()
    if token_in_addr == victim_pool.token0_address:
        victim_direction = "0to1"
        reserve_in = victim_pool.reserve0
        reserve_out = victim_pool.reserve1
        profit_symbol = victim_pool.token1_symbol  # round-trip ends in token1 (B)
    elif token_in_addr == victim_pool.token1_address:
        victim_direction = "1to0"
        reserve_in = victim_pool.reserve1
        reserve_out = victim_pool.reserve0
        profit_symbol = victim_pool.token0_symbol  # round-trip ends in token0 (B)
    else:
        # path[0] matches neither pool token — shouldn't happen given that the
        # pool was looked up by this same path, but defend against it anyway.
        return None

    # ── 5. Simulate victim's swap to get post-swap reserves ───────────────────
    _, new_reserve_in, new_reserve_out = simulate_swap(amount_in, reserve_in, reserve_out)
    # new_reserve_in  = abundant/cheap side (A — victim's input token)
    # new_reserve_out = depleted/expensive side (B — victim's output token)

    # ── 6. Find counterparty pools ────────────────────────────────────────────
    counterparties = registry.get_counterparty_pools(victim_pool)
    if not counterparties:
        return None

    # ── 7. Find the most profitable backrun across all counterparties ─────────
    # Counter-intuitive but correct: after the victim swap, B is "scarce" on the
    # victim pool, which means each unit of B we sell to the pool buys MORE A
    # than before.  The victim displaced the price in our favor for this direction.
    best_amount = 0
    best_profit = 0
    best_cp: Pool | None = None

    for cp in counterparties:
        if cp.reserve0 == 0 or cp.reserve1 == 0:
            continue

        # Leg 1 on victim pool: sell B (depleted side) to buy cheap A.
        rin_a = new_reserve_out
        rout_a = new_reserve_in

        # Leg 2 on counterparty: sell A to buy B at the undisturbed price.
        if victim_direction == "0to1":
            # A = token0, B = token1
            rin_b = cp.reserve0
            rout_b = cp.reserve1
        else:
            # A = token1, B = token0
            rin_b = cp.reserve1
            rout_b = cp.reserve0

        amount, profit = optimal_two_pool_arb(rin_a, rout_a, rin_b, rout_b)
        if profit > best_profit:
            best_profit = profit
            best_amount = amount
            best_cp = cp

    if best_cp is None or best_profit <= 0:
        return None

    # ── 8. Build and return the opportunity dict ──────────────────────────────
    sym0 = victim_pool.token0_symbol
    sym1 = victim_pool.token1_symbol

    return {
        "victim_pool_address": victim_pool.address,
        "victim_pool_dex": victim_pool.dex,
        "counterparty_pool_address": best_cp.address,
        "counterparty_pool_dex": best_cp.dex,
        "token_pair": f"{sym0}/{sym1}",
        "victim_amount_in": amount_in,
        "victim_direction": victim_direction,
        "backrun_amount_in": best_amount,
        "backrun_profit": best_profit,
        "backrun_profit_token": profit_symbol,
    }