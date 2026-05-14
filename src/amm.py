"""
Pure Uniswap v2 AMM math.  No I/O, no web3, no logging.

All integer functions replicate on-chain Solidity semantics exactly:
  - truncating (floor) division via //
  - the 0.3 % fee encoded as the 997/1000 factor used in the contracts
"""

import math


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    """
    Return the output amount for a swap, matching UniswapV2Library.getAmountOut.

    On-chain formula (Solidity):
        uint amountInWithFee = amountIn * 997;
        uint numerator       = amountInWithFee * reserveOut;
        uint denominator     = reserveIn * 1000 + amountInWithFee;
        amountOut            = numerator / denominator;   // truncating

    The 997/1000 ratio encodes the 0.3 % LP fee.
    """
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        raise ValueError("amounts and reserves must be positive")
    amount_in_with_fee = amount_in * 997
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * 1000 + amount_in_with_fee
    return numerator // denominator


def get_amount_in(amount_out: int, reserve_in: int, reserve_out: int) -> int:
    """
    Return the input amount required to receive exactly amount_out, matching
    UniswapV2Library.getAmountIn.

    On-chain formula (Solidity):
        uint numerator   = reserveIn * amountOut * 1000;
        uint denominator = (reserveOut - amountOut) * 997;
        amountIn         = numerator / denominator + 1;  // + 1 rounds up

    The trailing + 1 is the Solidity contract's explicit ceiling-division step
    that ensures the pool is never left short.
    """
    if amount_out <= 0 or reserve_in <= 0 or reserve_out <= 0:
        raise ValueError("amounts and reserves must be positive")
    if amount_out >= reserve_out:
        raise ValueError("amount_out must be less than reserve_out")
    numerator = reserve_in * amount_out * 1000
    denominator = (reserve_out - amount_out) * 997
    return numerator // denominator + 1


def simulate_swap(
    amount_in: int, reserve_in: int, reserve_out: int
) -> tuple[int, int, int]:
    """
    Apply a swap to a pool and return the new state.

    Returns (amount_out, new_reserve_in, new_reserve_out).

    Use this to model the pool state after a victim tx lands so that the
    backrun calculation starts from the correct post-swap reserves.
    """
    amount_out = get_amount_out(amount_in, reserve_in, reserve_out)
    return amount_out, reserve_in + amount_in, reserve_out - amount_out


def price_after_swap(amount_in: int, reserve_in: int, reserve_out: int) -> float:
    """
    Return the mid-price new_reserve_out / new_reserve_in after a swap.

    This is a float for display and comparison only.  Never feed the result
    back into the integer math functions.
    """
    _, new_reserve_in, new_reserve_out = simulate_swap(amount_in, reserve_in, reserve_out)
    return new_reserve_out / new_reserve_in

# in v1 of this script, external price was an input, and the function returned the price of the pool's token pair relative to the price of the input pool.
# But based on how the ABI returns token details, there wasn't a clear way to determine the correct direction the arb should be executed.
#the current method allows us straight integer comparison between the two pools, giving us the exact amount of direction of the arb regardless of magnitude.
def find_arb_direction(
    reserve0_a: int, reserve1_a: int, reserve0_b: int, reserve1_b: int
) -> int:
    """
    Given two pools sharing the same token0/token1, return which swap direction
    is profitable, or 0 if neither is.

    Return values:
        +1  swap token0 → token1 on pool A, token1 → token0 on pool B
        -1  swap token1 → token0 on pool A, token0 → token1 on pool B
         0  no profitable arb exists in either direction

    A round-trip pays two 0.3 % fees (≈ 0.6 % total).  Arb in the +1 direction
    is profitable iff the fee-adjusted product of mid-prices exceeds 1:

        (997/1000)² · (R1_A/R0_A) · (R0_B/R1_B) > 1

    Rearranged into pure integer form (no division, no floats):

        997² · R1_A · R0_B  >  1000² · R0_A · R1_B   →  +1
        997² · R0_A · R1_B  >  1000² · R1_A · R0_B   →  -1
    """
    if reserve0_a <= 0 or reserve1_a <= 0 or reserve0_b <= 0 or reserve1_b <= 0:
        raise ValueError("reserves must be positive")
    if 997 * 997 * reserve1_a * reserve0_b > 1000 * 1000 * reserve0_a * reserve1_b:
        return 1
    if 997 * 997 * reserve0_a * reserve1_b > 1000 * 1000 * reserve1_a * reserve0_b:
        return -1
    return 0

#fixed an issue here to prevent floor(x*) from returning an integer that isn't the best int, since Solidity has a decimal limit.
# this checks both candidates
def optimal_two_pool_arb(
    reserve_in_a: int, reserve_out_a: int, reserve_in_b: int, reserve_out_b: int
) -> tuple[int, int]:
    """
    Find the optimal input and profit for a two-pool arb.

    Leg 1: swap token_in → token_out on pool A  (reserve_in_a, reserve_out_a)
    Leg 2: swap token_out → token_in on pool B  (reserve_in_b, reserve_out_b)

    Both pools must be oriented consistently: reserve_in_b holds the token that
    comes out of pool A; reserve_out_b holds the token that goes into pool A.
    All values must be in the same raw integer units — no decimal adjustment.

    Returns (amount_in_a, profit_in_token_in_units).
    Returns (0, 0) when no profitable arb exists.

    Derivation
    ----------
    Let a = 997, b = 1000.  Composing both get_amount_out calls:

        out_A(x) = a·x·Rout_A / (b·Rin_A + a·x)

        out_B(out_A(x)) = a²·x·Rout_A·Rout_B
                          ──────────────────────────────────────────
                          b²·Rin_A·Rin_B + a·x·(b·Rin_B + a·Rout_A)

    Define constants:
        N  = a² · Rout_A · Rout_B          = 997²  · Rout_A · Rout_B
        D0 = b² · Rin_A  · Rin_B           = 1000² · Rin_A  · Rin_B
        D1 = a  · (b·Rin_B + a·Rout_A)     = 997   · (1000·Rin_B + 997·Rout_A)

    Profit:
        P(x) = N·x / (D0 + D1·x) - x

    Setting dP/dx = 0:
        N·D0 / (D0 + D1·x)² = 1
        D0 + D1·x = √(N·D0)
        x* = (√(N·D0) - D0) / D1

    x* > 0 iff N > D0 — the same condition find_arb_direction uses.

    Because x* is real-valued and AMM math uses integer truncation, both
    floor(x*) and floor(x*)+1 are evaluated; the one with higher actual profit
    (computed via get_amount_out) is returned.
    """
    N = 997 * 997 * reserve_out_a * reserve_out_b
    D0 = 1000 * 1000 * reserve_in_a * reserve_in_b
    if N <= D0:
        return 0, 0

    D1 = 997 * (1000 * reserve_in_b + 997 * reserve_out_a)
    x_star = (math.sqrt(N * D0) - D0) / D1

    best_amount, best_profit = 0, 0
    for x in (int(x_star), int(x_star) + 1):
        if x <= 0:
            continue
        mid = get_amount_out(x, reserve_in_a, reserve_out_a)
        if mid <= 0:
            continue
        final = get_amount_out(mid, reserve_in_b, reserve_out_b)
        profit = final - x
        if profit > best_profit:
            best_amount, best_profit = x, profit

    return best_amount, best_profit
