"""
Tests for src/amm.py.

These tests verify both that the math matches on-chain Solidity semantics
(integer truncation, fee handling) and that the two-pool arb optimizer
is internally consistent. Direction agrees with profitability, returned
amounts are locally optimal, profits match composed get_amount_out calls, etc.
"""

import pytest

from src.amm import (
    find_arb_direction,
    get_amount_in,
    get_amount_out,
    optimal_two_pool_arb,
    price_after_swap,
    simulate_swap,
)

# Plausible WETH/USDC pool state.  token0=USDC (6 dec), token1=WETH (18 dec).
R0_USDC = 1_234_567 * 10**6   # 1,234,567 USDC
R1_WETH = 500 * 10**18         # 500 WETH


class TestGetAmountOut:
    def test_known_swap_matches_solidity(self):
        # Swap 1 WETH → USDC.  reserve_in=WETH, reserve_out=USDC.
        #
        # Manual derivation (matches Solidity integer truncation):
        #   amount_in_with_fee = 997 × 10^18
        #   numerator          = 997 × 1_234_567 × 10^24  = 1_230_863_299 × 10^24
        #   denominator        = (500_000 + 997) × 10^18  = 500_997 × 10^18
        #   amount_out         = 1_230_863_299 × 10^6 // 500_997 = 2_456_827_683
        assert get_amount_out(1 * 10**18, R1_WETH, R0_USDC) == 2_456_827_683

    def test_raises_on_zero_amount_in(self):
        with pytest.raises(ValueError):
            get_amount_out(0, R1_WETH, R0_USDC)

    def test_raises_on_negative_amount_in(self):
        with pytest.raises(ValueError):
            get_amount_out(-1, R1_WETH, R0_USDC)

    def test_raises_on_zero_reserve_in(self):
        with pytest.raises(ValueError):
            get_amount_out(10**18, 0, R0_USDC)

    def test_raises_on_zero_reserve_out(self):
        with pytest.raises(ValueError):
            get_amount_out(10**18, R1_WETH, 0)


class TestGetAmountIn:
    def test_inverse_property_several_sizes(self):
        # The correct round-trip direction for ceiling-division semantics:
        # if you compute how much input is needed to get y out, then supplying
        # exactly that input must yield at least y.  (The reverse,
        # get_amount_in(get_amount_out(x)) >= x, does NOT hold: get_amount_out
        # floors its result, so the reversed lookup can require less than x.)
        for amount_out in [1 * 10**6, 10 * 10**6, 100 * 10**6, 1_000 * 10**6]:
            required_in = get_amount_in(amount_out, R1_WETH, R0_USDC)
            actual_out = get_amount_out(required_in, R1_WETH, R0_USDC)
            assert actual_out >= amount_out, (
                f"round-trip failed for amount_out={amount_out}: "
                f"get_amount_in gave {required_in}, get_amount_out gave {actual_out}"
            )

    def test_raises_when_amount_out_equals_reserve(self):
        with pytest.raises(ValueError):
            get_amount_in(R0_USDC, R1_WETH, R0_USDC)

    def test_raises_when_amount_out_exceeds_reserve(self):
        with pytest.raises(ValueError):
            get_amount_in(R0_USDC + 1, R1_WETH, R0_USDC)

    def test_raises_on_zero_amount_out(self):
        with pytest.raises(ValueError):
            get_amount_in(0, R1_WETH, R0_USDC)


class TestSimulateSwap:
    def test_k_grows_after_fee_normal_swap(self):
        k_before = R1_WETH * R0_USDC
        out, new_rin, new_rout = simulate_swap(1 * 10**18, R1_WETH, R0_USDC)
        assert new_rin * new_rout > k_before

    def test_k_grows_after_fee_tiny_swap(self):
        # Even a dust-sized swap must grow k (LP always earns the fee).
        k_before = R1_WETH * R0_USDC
        out, new_rin, new_rout = simulate_swap(10**9, R1_WETH, R0_USDC)
        assert new_rin * new_rout > k_before

    def test_reserve_accounting(self):
        amount_in = 2 * 10**18
        amount_out, new_rin, new_rout = simulate_swap(amount_in, R1_WETH, R0_USDC)
        assert new_rin == R1_WETH + amount_in
        assert new_rout == R0_USDC - amount_out


class TestPriceAfterSwap:
    def test_returns_float(self):
        result = price_after_swap(10**18, R1_WETH, R0_USDC)
        assert isinstance(result, float)

    def test_price_falls_when_reserve_out_depleted(self):
        # Swapping WETH in → USDC out.  Pool gains WETH, loses USDC.
        # Mid-price (USDC-units / WETH-units) must decrease.
        price_before = R0_USDC / R1_WETH
        price_after = price_after_swap(1 * 10**18, R1_WETH, R0_USDC)
        assert isinstance(price_after, float)
        assert price_after < price_before

    def test_larger_swap_moves_price_more(self):
        small = price_after_swap(10**17, R1_WETH, R0_USDC)
        large = price_after_swap(10 * 10**18, R1_WETH, R0_USDC)
        assert large < small


class TestFindArbDirection:
    def test_identical_pools_returns_zero(self):
        assert find_arb_direction(1_000_000, 2_000_000, 1_000_000, 2_000_000) == 0

    def test_direction_positive_when_pool_a_has_higher_token0_price(self):
        # Pool A: 3 t1 per t0  (t0 is expensive on A — sell t0 there)
        # Pool B: 2 t1 per t0  (t0 is cheap on B — buy t0 there)
        # Profit direction: t0→t1 on A, t1→t0 on B  →  +1
        assert find_arb_direction(1_000_000, 3_000_000, 1_000_000, 2_000_000) == 1

    def test_direction_negative_when_pool_b_has_higher_token0_price(self):
        # Same pools, swapped: now t0 is expensive on B.
        # Profit direction: t1→t0 on A, t0→t1 on B  →  -1
        assert find_arb_direction(1_000_000, 2_000_000, 1_000_000, 3_000_000) == -1

    def test_swapping_pool_args_flips_sign(self):
        d1 = find_arb_direction(1_000_000, 3_000_000, 1_000_000, 2_000_000)
        d2 = find_arb_direction(1_000_000, 2_000_000, 1_000_000, 3_000_000)
        assert d1 == -d2

    def test_near_parity_within_fee_threshold_returns_zero(self):
        # Pool A price = 1.003, Pool B price = 1.000 → spread ≈ 0.3 %
        # Round-trip fee costs ≈ 0.6 %, so no profitable arb exists.
        # This also guards against the naive "rhs > lhs → return -1" shortcut,
        # which would incorrectly return -1 here.
        assert find_arb_direction(1_000_000, 1_003_000, 1_000_000, 1_000_000) == 0


class TestOptimalTwoPoolArb:
    def test_identical_pools_returns_zero(self):
        # +1 orientation on two identical pools
        amount, profit = optimal_two_pool_arb(1_000_000, 2_000_000, 2_000_000, 1_000_000)
        assert (amount, profit) == (0, 0)

    def test_consistent_with_find_arb_direction_zero(self):
        # Spread < fee threshold → find_arb_direction == 0, optimal must agree.
        R0A, R1A = 1_000_000, 1_003_000
        R0B, R1B = 1_000_000, 1_000_000
        assert find_arb_direction(R0A, R1A, R0B, R1B) == 0
        # +1 orientation
        assert optimal_two_pool_arb(R0A, R1A, R1B, R0B) == (0, 0)
        # -1 orientation
        assert optimal_two_pool_arb(R1A, R0A, R0B, R1B) == (0, 0)

    def test_profit_positive_when_arb_exists(self):
        # Pool A: 3 t1 per t0,  Pool B: 2 t1 per t0  →  clear +1 arb
        amount, profit = optimal_two_pool_arb(1_000_000, 3_000_000, 2_000_000, 1_000_000)
        assert amount > 0
        assert profit > 0

    def test_returned_profit_matches_get_amount_out_composition(self):
        # The profit field must equal what you'd actually receive on-chain.
        rin_a, rout_a = 1_000_000, 3_000_000
        rin_b, rout_b = 2_000_000, 1_000_000
        amount, profit = optimal_two_pool_arb(rin_a, rout_a, rin_b, rout_b)
        assert amount > 0
        mid = get_amount_out(amount, rin_a, rout_a)
        final = get_amount_out(mid, rin_b, rout_b)
        assert final - amount == profit

    def test_returned_amount_is_local_optimum(self):
        # Neither amount-1 nor amount+1 should yield strictly more profit.
        rin_a, rout_a = 1_000_000, 3_000_000
        rin_b, rout_b = 2_000_000, 1_000_000
        amount, profit = optimal_two_pool_arb(rin_a, rout_a, rin_b, rout_b)
        assert amount > 0

        def actual_profit(x: int) -> int:
            if x <= 0:
                return -1
            mid = get_amount_out(x, rin_a, rout_a)
            return get_amount_out(mid, rin_b, rout_b) - x

        assert profit >= actual_profit(amount - 1)
        assert profit >= actual_profit(amount + 1)
