"""
Tests for src/opportunity.py.

Uses a FakeRegistry stub so there are no web3 calls or real PoolRegistry
machinery.  Reserve values were chosen so that:
  - 100 WETH victim (1to0) on BASE reserves → profitable arb exists
  - 100_000 USDC victim (0to1) on BASE reserves → profitable arb exists
  - 1 WETH victim (1to0) on BASE reserves with equal Sushi → no arb (tiny price impact)
All three were verified numerically via amm.optimal_two_pool_arb before being
hardcoded here.
"""

from src.amm import get_amount_out, simulate_swap
from src.opportunity import detect_opportunity
from src.pools import Pool

# ── addresses (real, lowercased) ──────────────────────────────────────────────
USDC_ADDR = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH_ADDR = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
WBTC_ADDR = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
UNI_ADDR  = "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"
SUSHI_ADDR = "0x397ff1542f962076d0bfe58ea045ffa2d347aca0"
DUMMY_RECIPIENT = "0x" + "ab" * 20

# ── base reserves: 1 M USDC / 500 WETH (token0=USDC, token1=WETH) ────────────
BASE_USDC = 1_000_000 * 10**6   # raw USDC units
BASE_WETH = 500 * 10**18         # raw WETH units

# ── pre-canned victim swap dicts ──────────────────────────────────────────────
# Direction 1to0: victim sells WETH → USDC (path starts with token1 = WETH)
VICTIM_WETH_FOR_USDC = {
    "function_name": "swapExactTokensForTokens",
    "amount_in": 100 * 10**18,        # 100 WETH
    "amount_out_min": 1,
    "path": [WETH_ADDR, USDC_ADDR],
    "recipient": DUMMY_RECIPIENT,
    "deadline": 9_999_999_999,
}

# Direction 0to1: victim sells USDC → WETH (path starts with token0 = USDC)
VICTIM_USDC_FOR_WETH = {
    "function_name": "swapExactTokensForTokens",
    "amount_in": 100_000 * 10**6,     # 100 K USDC
    "amount_out_min": 1,
    "path": [USDC_ADDR, WETH_ADDR],
    "recipient": DUMMY_RECIPIENT,
    "deadline": 9_999_999_999,
}


# ── minimal FakeRegistry ──────────────────────────────────────────────────────

class FakeRegistry:
    """
    Stub implementing only the two methods detect_opportunity calls.
    find_pool_by_router_path matches any 2-element path whose token set equals
    the victim pool's token set; returns None otherwise.
    """

    def __init__(self, victim_pool: Pool | None, counterparty_pools: list[Pool]) -> None:
        self._victim = victim_pool
        self._counterparties = counterparty_pools

    def find_pool_by_router_path(self, path: list[str]) -> Pool | None:
        if len(path) != 2 or self._victim is None:
            return None
        queried = frozenset(a.lower() for a in path)
        owned = frozenset([self._victim.token0_address, self._victim.token1_address])
        return self._victim if queried == owned else None

    def get_counterparty_pools(self, pool: Pool) -> list[Pool]:
        return self._counterparties


def _make_pool(address: str, dex: str, r0: int, r1: int) -> Pool:
    return Pool(
        address=address,
        dex=dex,
        token0_address=USDC_ADDR,
        token0_symbol="USDC",
        token0_decimals=6,
        token1_address=WETH_ADDR,
        token1_symbol="WETH",
        token1_decimals=18,
        reserve0=r0,
        reserve1=r1,
    )


def make_registry(
    uni_reserves: tuple[int, int],
    sushi_reserves: tuple[int, int] | None = None,
) -> FakeRegistry:
    """
    Build a FakeRegistry with a Uniswap USDC/WETH victim pool and an optional
    SushiSwap counterparty.  Both pools share the same token0=USDC, token1=WETH
    layout, matching the real on-chain ordering.
    """
    uni = _make_pool(UNI_ADDR, "uniswap_v2", *uni_reserves)
    counterparties: list[Pool] = []
    if sushi_reserves is not None:
        counterparties = [_make_pool(SUSHI_ADDR, "sushiswap_v2", *sushi_reserves)]
    return FakeRegistry(victim_pool=uni, counterparty_pools=counterparties)


# ── happy-path tests ──────────────────────────────────────────────────────────

class TestHappyPath:
    def test_returns_opportunity_when_arb_profitable(self):
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        opp = detect_opportunity(VICTIM_WETH_FOR_USDC, registry)

        assert opp is not None
        # Every required key must be present.
        for key in (
            "victim_pool_address", "victim_pool_dex",
            "counterparty_pool_address", "counterparty_pool_dex",
            "token_pair", "victim_amount_in", "victim_direction",
            "backrun_amount_in", "backrun_profit", "backrun_profit_token",
        ):
            assert key in opp, f"missing key: {key}"

        assert opp["victim_pool_address"]       == UNI_ADDR
        assert opp["victim_pool_dex"]           == "uniswap_v2"
        assert opp["counterparty_pool_address"] == SUSHI_ADDR
        assert opp["counterparty_pool_dex"]     == "sushiswap_v2"
        assert opp["token_pair"]                == "USDC/WETH"
        assert opp["victim_amount_in"]          == 100 * 10**18
        assert opp["victim_direction"]          == "1to0"
        assert opp["backrun_profit_token"]      == "USDC"
        assert opp["backrun_amount_in"]         > 0
        assert opp["backrun_profit"]            > 0

    def test_returned_profit_matches_get_amount_out_composition(self):
        # Re-derive the backrun profit independently from first principles and
        # compare against the value detect_opportunity reports.
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        opp = detect_opportunity(VICTIM_WETH_FOR_USDC, registry)
        assert opp is not None

        # Victim direction "1to0": reserve_in=WETH, reserve_out=USDC.
        _, new_r1, new_r0 = simulate_swap(
            VICTIM_WETH_FOR_USDC["amount_in"], BASE_WETH, BASE_USDC
        )
        # Backrun leg 1 on Uniswap post-victim: sell USDC (thin side), get WETH.
        rin_a, rout_a = new_r0, new_r1
        # Backrun leg 2 on Sushi: sell WETH, get USDC.
        # For direction 1to0: rin_b=cp.reserve1 (WETH), rout_b=cp.reserve0 (USDC).
        rin_b, rout_b = BASE_WETH, BASE_USDC

        x = opp["backrun_amount_in"]
        mid   = get_amount_out(x,   rin_a, rout_a)
        final = get_amount_out(mid, rin_b, rout_b)
        assert final - x == opp["backrun_profit"]

    def test_victim_direction_1to0_when_path_starts_with_token1(self):
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        opp = detect_opportunity(VICTIM_WETH_FOR_USDC, registry)
        assert opp is not None
        assert opp["victim_direction"]     == "1to0"
        assert opp["backrun_profit_token"] == "USDC"   # profit in victim's output token
        assert opp["backrun_profit"]       > 0

    def test_victim_direction_0to1_when_path_starts_with_token0(self):
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        opp = detect_opportunity(VICTIM_USDC_FOR_WETH, registry)
        assert opp is not None
        assert opp["victim_direction"]     == "0to1"
        assert opp["backrun_profit_token"] == "WETH"   # profit in victim's output token
        assert opp["backrun_profit"]       > 0


# ── None-returning tests ──────────────────────────────────────────────────────

class TestReturnsNone:
    def test_multi_hop_path_returns_none(self):
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        swap = {**VICTIM_WETH_FOR_USDC, "path": [WETH_ADDR, USDC_ADDR, WBTC_ADDR]}
        assert detect_opportunity(swap, registry) is None

    def test_unknown_pair_returns_none(self):
        # Registry knows USDC/WETH; victim path is WBTC/WETH — no match.
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        swap = {**VICTIM_WETH_FOR_USDC, "path": [WBTC_ADDR, WETH_ADDR]}
        assert detect_opportunity(swap, registry) is None

    def test_no_counterparty_returns_none(self):
        # Only one pool for the pair — no venue to arb against.
        registry = make_registry(uni_reserves=(BASE_USDC, BASE_WETH))  # no sushi
        assert detect_opportunity(VICTIM_WETH_FOR_USDC, registry) is None

    def test_no_arb_when_pools_identical_and_victim_tiny(self):
        # A 1-WETH victim into a 500-WETH pool (0.2 % impact) falls below the
        # 0.6 % round-trip fee threshold → optimal_two_pool_arb returns (0, 0).
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        tiny_swap = {**VICTIM_WETH_FOR_USDC, "amount_in": 1 * 10**18}
        assert detect_opportunity(tiny_swap, registry) is None

    def test_returns_none_when_amount_in_and_override_both_none(self):
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        swap = {**VICTIM_WETH_FOR_USDC, "amount_in": None}
        # No override provided either.
        assert detect_opportunity(swap, registry, amount_in_override=None) is None

    def test_returns_none_when_victim_pool_reserves_zero(self):
        registry = make_registry(
            uni_reserves=(0, BASE_WETH),              # reserve0 = 0
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        assert detect_opportunity(VICTIM_WETH_FOR_USDC, registry) is None


# ── ETH-variant tests ─────────────────────────────────────────────────────────

class TestETHVariant:
    def test_uses_override_when_decoded_amount_in_is_none(self):
        # swapExactETHForTokens doesn't encode amount_in in calldata;
        # the caller passes it via amount_in_override from tx['value'].
        registry = make_registry(
            uni_reserves=(BASE_USDC, BASE_WETH),
            sushi_reserves=(BASE_USDC, BASE_WETH),
        )
        swap = {
            "function_name": "swapExactETHForTokens",
            "amount_in": None,                        # ← always None from decoder
            "amount_out_min": 1,
            "path": [WETH_ADDR, USDC_ADDR],
            "recipient": DUMMY_RECIPIENT,
            "deadline": 9_999_999_999,
        }
        opp = detect_opportunity(swap, registry, amount_in_override=100 * 10**18)

        assert opp is not None
        # The opportunity should be identical to the non-ETH case with the same amount.
        assert opp["victim_amount_in"]     == 100 * 10**18
        assert opp["victim_direction"]     == "1to0"
        assert opp["backrun_profit"]       > 0
        assert opp["backrun_profit_token"] == "USDC"
