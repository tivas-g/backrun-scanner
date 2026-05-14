from src.decoder import decode_swap

# Real swapExactTokensForTokens tx (1 WETH → USDC, sourced from Uniswap v2 router history).
# Verified field-by-field against eth_abi.decode output.
SWAP_EXACT_TOKENS_FOR_TOKENS = (
    "0x38ed1739"
    "0000000000000000000000000000000000000000000000000de0b6b3a7640000"
    "0000000000000000000000000000000000000000000000000000000000a86bbe"
    "00000000000000000000000000000000000000000000000000000000000000a0"
    "00000000000000000000000037305b1cd40574e4c5ce33f8e8306be057fd7341"
    "000000000000000000000000000000000000000000000000000000006a4d2b31"
    "0000000000000000000000000000000000000000000000000000000000000002"
    "000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    "000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
)

# Synthetic swapExactETHForTokens: amountOutMin=1e9 USDC-units, path=[WETH,USDC],
# to=0x1234..., deadline=1783442225.  Generated via eth_abi.encode.
SWAP_EXACT_ETH_FOR_TOKENS = (
    "0x7ff36ab5"
    "000000000000000000000000000000000000000000000000000000003b9aca00"
    "0000000000000000000000000000000000000000000000000000000000000080"
    "0000000000000000000000001234567890123456789012345678901234567890"
    "000000000000000000000000000000000000000000000000000000006a4d2b31"
    "0000000000000000000000000000000000000000000000000000000000000002"
    "000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    "000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
)


class TestDecodeSwapExactTokensForTokens:
    def test_all_fields_decoded_correctly(self):
        result = decode_swap(SWAP_EXACT_TOKENS_FOR_TOKENS)
        assert result is not None
        assert result["function_name"] == "swapExactTokensForTokens"
        assert result["amount_in"] == 1_000_000_000_000_000_000   # 1 WETH
        assert result["amount_out_min"] == 11_037_630              # 11.03763 USDC
        assert result["path"] == [
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
        ]
        assert result["recipient"] == "0x37305b1cd40574e4c5ce33f8e8306be057fd7341"
        assert result["deadline"] == 1_783_442_225

    def test_addresses_are_lowercase(self):
        result = decode_swap(SWAP_EXACT_TOKENS_FOR_TOKENS)
        for addr in result["path"]:
            assert addr == addr.lower()
        assert result["recipient"] == result["recipient"].lower()

    def test_works_without_0x_prefix(self):
        stripped = SWAP_EXACT_TOKENS_FOR_TOKENS.removeprefix("0x")
        result = decode_swap(stripped)
        assert result is not None
        assert result["function_name"] == "swapExactTokensForTokens"


class TestDecodeSwapExactETHForTokens:
    def test_amount_in_is_none(self):
        # ETH value is in tx['value'], not calldata — decoder must signal this explicitly.
        result = decode_swap(SWAP_EXACT_ETH_FOR_TOKENS)
        assert result is not None
        assert result["function_name"] == "swapExactETHForTokens"
        assert result["amount_in"] is None

    def test_remaining_fields_decoded(self):
        result = decode_swap(SWAP_EXACT_ETH_FOR_TOKENS)
        assert result["amount_out_min"] == 1_000_000_000
        assert result["path"] == [
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        ]
        assert result["recipient"] == "0x1234567890123456789012345678901234567890"
        assert result["deadline"] == 1_783_442_225


class TestDecodeSwapRejectsUnsupported:
    def test_unknown_selector_returns_none(self):
        # 0xa9059cbb is ERC-20 transfer — not a swap
        unknown = "0xa9059cbb" + "00" * 64
        assert decode_swap(unknown) is None

    def test_too_short_returns_none(self):
        assert decode_swap("0x38ed17") is None   # only 3 bytes, selector incomplete
        assert decode_swap("0x") is None
        assert decode_swap("") is None

    def test_garbage_hex_returns_none(self):
        assert decode_swap("0xzzzzzzzz") is None

    def test_valid_selector_truncated_payload_returns_none(self):
        # Selector matches but calldata is too short to decode
        assert decode_swap("0x38ed1739" + "deadbeef") is None
