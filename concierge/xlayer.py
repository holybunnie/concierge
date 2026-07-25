"""X Layer mainnet (196) adapter (receipt anchoring) — signs and anchors receipts for real.

`receipts.py` writes `signature` and `xlayer_tx` as NULL until this module exists (see its
docstring). This fills that seam: every anchor is a real `anchorReceipt(bytes32)` call against
the deployed `ReceiptAnchor` contract, signed by the operator's funded key, confirmed by polling
the transaction receipt — never assumed from a broadcast succeeding. Mainnet only, chain 196
(docs/VERIFICATION_LEDGER.md §9): a testnet anchor proves nothing to a customer or an arbitrator.

Standard library for the RPC transport, like postmark.py and calcom.py — this signs commitments,
so every dependency is weighed. `eth_account` is the one exception: hand-rolling secp256k1
signing and RLP encoding to avoid one audited, widely-used dependency is a worse security
trade-off than taking the dependency.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from eth_account import Account
from eth_utils import keccak, to_checksum_address

from . import config

ANCHOR_RECEIPT_SELECTOR = keccak(text="anchorReceipt(bytes32)")[:4]
ANCHORED_EVENT_TOPIC = keccak(text="Anchored(uint256,bytes32,address,uint256)")


class ChainError(RuntimeError):
    """The chain call did not confirm success. Never swallowed into a fake tx hash."""


def _rpc(method: str, params: list) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(
        config.xlayer_rpc(), data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise ChainError(f"RPC {method} → HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e
    if "error" in body:
        raise ChainError(f"RPC {method} → {body['error']}")
    return body["result"]


def chain_id() -> int:
    return int(_rpc("eth_chainId", []), 16)


def _account() -> Account:
    key = config.xlayer_private_key()
    if not key:
        raise ChainError(
            "XLAYER_PRIVATE_KEY is not set. CONCIERGE will not anchor without a real funded "
            "signer — see docs/OPERATOR_PROVIDES.md item 6."
        )
    return Account.from_key(key)


def address() -> str:
    """The signer's own address, for balance checks and deploy record-keeping. Never the key."""
    return _account().address


def balance_wei() -> int:
    return int(_rpc("eth_getBalance", [address(), "latest"]), 16)


@dataclass(frozen=True)
class TxResult:
    tx_hash: str
    block_number: int
    gas_used: int
    status: bool                 # True = the EVM itself reports success, not just "was mined"
    contract_address: str | None # set only for a deploy
    logs: list[dict]


def _send(*, to: str | None, data: bytes, value: int = 0, gas_limit_buffer: float = 1.2) -> TxResult:
    """Sign, broadcast, and wait for one transaction. Confirms via the receipt's own `status`
    field — never assumes success from a broadcast being accepted."""
    acct = _account()
    cid = chain_id()
    if cid != config.XLAYER_MAINNET_CHAIN_ID:
        raise ChainError(
            f"Connected chain is {cid}, not X Layer mainnet ({config.XLAYER_MAINNET_CHAIN_ID}). "
            "Receipts anchor on mainnet only — refusing to sign."
        )

    nonce = int(_rpc("eth_getTransactionCount", [acct.address, "pending"]), 16)
    gas_price = int(_rpc("eth_gasPrice", []), 16)
    estimate_call = {"from": acct.address, "data": "0x" + data.hex(), "value": hex(value)}
    if to:
        estimate_call["to"] = to
    estimated_gas = int(_rpc("eth_estimateGas", [estimate_call]), 16)
    gas_limit = int(estimated_gas * gas_limit_buffer)

    tx = {
        "nonce": nonce,
        "gasPrice": gas_price,
        "gas": gas_limit,
        "value": value,
        "data": data,
        "chainId": cid,
    }
    if to:
        tx["to"] = to
    signed = Account.sign_transaction(tx, acct.key)
    tx_hash = _rpc("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])

    receipt = _wait_for_receipt(tx_hash)
    status = int(receipt["status"], 16) == 1
    if not status:
        raise ChainError(f"Transaction {tx_hash} was mined but reverted (status=0). No fake success.")

    return TxResult(
        tx_hash=tx_hash,
        block_number=int(receipt["blockNumber"], 16),
        gas_used=int(receipt["gasUsed"], 16),
        status=status,
        contract_address=(to_checksum_address(receipt["contractAddress"])
                           if receipt.get("contractAddress") else None),
        logs=receipt.get("logs", []),
    )


def _wait_for_receipt(tx_hash: str, *, timeout_s: float = 90.0, poll_s: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        receipt = _rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            return receipt
        time.sleep(poll_s)
    raise ChainError(f"Transaction {tx_hash} did not confirm within {timeout_s}s.")


def deploy(bytecode_hex: str) -> TxResult:
    """Deploy ReceiptAnchor. One-off; the resulting address is meant to be saved as
    XLAYER_CONTRACT and reused, not redeployed per anchor."""
    code = bytecode_hex[2:] if bytecode_hex.startswith("0x") else bytecode_hex
    return _send(to=None, data=bytes.fromhex(code))


def anchor(content_hash_hex: str) -> TxResult:
    """Call anchorReceipt(bytes32) on the deployed contract with a receipt's content hash."""
    contract = config.xlayer_contract()
    if not contract:
        raise ChainError(
            "XLAYER_CONTRACT is not set — no ReceiptAnchor deployment to anchor against."
        )
    h = content_hash_hex[2:] if content_hash_hex.startswith("0x") else content_hash_hex
    if len(h) != 64:
        raise ChainError(f"content_hash must be 32 bytes (64 hex chars), got {len(h)}.")
    data = ANCHOR_RECEIPT_SELECTOR + bytes.fromhex(h)
    return _send(to=contract, data=data)


def hash_of(anchor_id: int) -> str:
    """Read back an anchored hash via eth_call — the on-chain source of truth, not the DB."""
    contract = config.xlayer_contract()
    if not contract:
        raise ChainError("XLAYER_CONTRACT is not set.")
    selector = keccak(text="hashOf(uint256)")[:4]
    data = selector + anchor_id.to_bytes(32, "big")
    result = _rpc("eth_call", [{"to": contract, "data": "0x" + data.hex()}, "latest"])
    return result[2:] if result.startswith("0x") else result
