"""
Decode Uniswap v2 router swap calldata into a structured dict.

Only the three exact-input variants are supported; all others return None.
Amount denominations are raw integers (no decimal adjustment).
"""

from eth_abi import decode

# 4-byte selectors → function names
_SELECTORS: dict[str, str] = {
    "38ed1739": "swapExactTokensForTokens",
    "7ff36ab5": "swapExactETHForTokens",
    "18cbafe5": "swapExactTokensForETH",
}

# ABI parameter types for each function (selector stripped, path = address[])
_ABI_TYPES: dict[str, list[str]] = {
    "swapExactTokensForTokens": ["uint256", "uint256", "address[]", "address", "uint256"],
    "swapExactETHForTokens":    ["uint256", "address[]", "address", "uint256"],
    "swapExactTokensForETH":    ["uint256", "uint256", "address[]", "address", "uint256"],
}


def decode_swap(input_data: str) -> dict | None:
    """
    Decode the calldata of a Uniswap v2 router swap transaction.

    input_data: tx['input'] hex string, with or without leading '0x'.

    Returns a dict with keys:
        function_name   str   one of the three supported function names
        amount_in       int | None
                              raw token units of the input; None for
                              swapExactETHForTokens (caller must read tx['value'])
        amount_out_min  int   slippage floor in raw output-token units
        path            list[str]   token addresses, lowercased, '0x'-prefixed
        recipient       str   lowercased 'to' address
        deadline        int   unix timestamp

    Returns None for any unrecognised selector or malformed input; never raises.
    """
    try:
        data = input_data.removeprefix("0x")
        if len(data) < 8:
            return None

        func_name = _SELECTORS.get(data[:8].lower())
        if func_name is None:
            return None

        payload = bytes.fromhex(data[8:])
        decoded = decode(_ABI_TYPES[func_name], payload)

        if func_name == "swapExactETHForTokens":
            amount_out_min, path, to, deadline = decoded
            amount_in = None
        else:
            amount_in, amount_out_min, path, to, deadline = decoded

        return {
            "function_name": func_name,
            "amount_in": amount_in,
            "amount_out_min": amount_out_min,
            "path": [addr.lower() for addr in path],
            "recipient": to.lower(),
            "deadline": deadline,
        }

    except Exception:
        return None
