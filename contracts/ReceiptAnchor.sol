// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// Anchors CONCIERGE decision receipts on X Layer (see docs/VERIFICATION_LEDGER.md §9).
/// The event is the durable, indexable record; the mapping exists so an anchor can be
/// confirmed by a contract call, not only by re-scanning logs. No access control: the
/// caller pays their own gas, and a foreign anchorReceipt call cannot alter or remove
/// CONCIERGE's own entries — ids are assigned by receiptCount, never reused.
contract ReceiptAnchor {
    event Anchored(
        uint256 indexed id,
        bytes32 indexed contentHash,
        address indexed anchoredBy,
        uint256 timestamp
    );

    mapping(uint256 => bytes32) public hashOf;
    uint256 public receiptCount;

    function anchorReceipt(bytes32 contentHash) external returns (uint256 id) {
        id = receiptCount++;
        hashOf[id] = contentHash;
        emit Anchored(id, contentHash, msg.sender, block.timestamp);
    }
}
