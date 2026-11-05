from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path
from datetime import timezone

from .config import get_settings
from .models import Observation, ResearchGrant, SourceBatch

CONTRACT_ABI = [
    {"inputs": [{"internalType": "bytes32", "name": "batchId", "type": "bytes32"}, {"internalType": "bytes32", "name": "commitment", "type": "bytes32"}, {"internalType": "string", "name": "schemaVersion", "type": "string"}, {"internalType": "bytes32", "name": "auditCommitment", "type": "bytes32"}], "name": "commitSourceBatch", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "batchId", "type": "bytes32"}], "name": "getBatch", "outputs": [{"components": [{"internalType": "bytes32", "name": "commitment", "type": "bytes32"}, {"internalType": "address", "name": "submitter", "type": "address"}, {"internalType": "uint64", "name": "submittedAt", "type": "uint64"}, {"internalType": "string", "name": "schemaVersion", "type": "string"}, {"internalType": "bytes32", "name": "auditCommitment", "type": "bytes32"}], "internalType": "struct SentinelIntegrity.Batch", "name": "", "type": "tuple"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "grantId", "type": "bytes32"}, {"internalType": "address", "name": "researcher", "type": "address"}, {"internalType": "bytes32", "name": "scopeHash", "type": "bytes32"}, {"internalType": "bytes32", "name": "purposeHash", "type": "bytes32"}, {"internalType": "uint64", "name": "expiresAt", "type": "uint64"}], "name": "grantResearch", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "grantId", "type": "bytes32"}], "name": "revokeResearch", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "grantId", "type": "bytes32"}, {"internalType": "address", "name": "researcher", "type": "address"}, {"internalType": "bytes32", "name": "scopeHash", "type": "bytes32"}, {"internalType": "bytes32", "name": "purposeHash", "type": "bytes32"}], "name": "isGrantActive", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
]


class ChainUnavailable(RuntimeError):
    pass


def manifest_for(observations: list[Observation]) -> list[dict]:
    rows = [{
        "id": item.id, "patient_id": item.patient_id, "code": item.code,
        "original_code": item.original_code, "coding_system": item.coding_system,
        "standard_code": item.standard_code, "terminology_status": item.terminology_status,
        "value": item.value, "unit": item.unit, "ucum_unit": item.ucum_unit,
        "normalised_value": item.normalised_value, "normalised_ucum_unit": item.normalised_ucum_unit,
        "result_status": item.result_status, "corrects_observation_id": item.corrects_observation_id,
        "observed_at": _canonical_time(item.observed_at), "source_id": item.source_id, "version": item.version,
    } for item in observations]
    return sorted(rows, key=lambda row: (row["source_id"], row["version"], row["id"]))


def _canonical_time(value):
    """SQLite may return a naive timestamp; canonical manifests always encode UTC explicitly."""
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()


def commitment_for(manifest: list[dict], nonce: str, schema_version: str = "sentinel-source-v1") -> str:
    canonical = json.dumps({"schema_version": schema_version, "nonce": nonce, "records": manifest}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_source_batch(observations: list[Observation]) -> SourceBatch:
    nonce = secrets.token_hex(32)
    manifest = manifest_for(observations)
    return SourceBatch(nonce=nonce, manifest=manifest, commitment=commitment_for(manifest, nonce))


def audit_commitment(events: list[dict]) -> str:
    return hashlib.sha256(json.dumps(events, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _contract():
    settings = get_settings()
    if not (settings.chain_rpc_url and settings.chain_private_key and settings.chain_contract_address):
        raise ChainUnavailable("Local development chain or Sepolia adapter is not configured.")
    from web3 import Web3
    web3 = Web3(Web3.HTTPProvider(settings.chain_rpc_url, request_kwargs={"timeout": 15}))
    if not web3.is_connected():
        raise ChainUnavailable("Configured blockchain RPC is unavailable.")
    return web3, web3.eth.contract(address=Web3.to_checksum_address(settings.chain_contract_address), abi=CONTRACT_ABI)


def _bytes32_text(web3, value: str) -> bytes:
    return bytes(web3.keccak(text=value))


def _send_and_confirm(web3, account, transaction) -> tuple[str, int]:
    signed = account.sign_transaction(transaction)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status != 1:
        raise ChainUnavailable("Chain transaction was mined without success.")
    return tx_hash.hex(), receipt.blockNumber


def _grant_identity(web3, grant: ResearchGrant) -> tuple[bytes, bytes, bytes]:
    return _bytes32_text(web3, f"sentinel-grant:{grant.id}"), _bytes32_text(web3, grant.scope), _bytes32_text(web3, grant.purpose)


def anchor_batch(batch: SourceBatch, audit_events: list[dict]) -> SourceBatch:
    web3, contract = _contract()
    settings = get_settings()
    account = web3.eth.account.from_key(settings.chain_private_key)
    batch_id = _bytes32_text(web3, f"sentinel-batch:{batch.id}")
    tx = contract.functions.commitSourceBatch(batch_id, bytes.fromhex(batch.commitment), batch.schema_version, bytes.fromhex(audit_commitment(audit_events))).build_transaction({"from": account.address, "nonce": web3.eth.get_transaction_count(account.address), "chainId": web3.eth.chain_id})
    tx_hash, block_number = _send_and_confirm(web3, account, tx)
    batch.on_chain_commitment = batch.commitment
    batch.chain_id = web3.eth.chain_id
    batch.contract_address = contract.address
    batch.transaction_hash = tx_hash.hex()
    batch.block_number = block_number
    batch.receipt_status = "confirmed"
    return batch


def verify_batch(batch: SourceBatch, observations: list[Observation]) -> dict:
    current = commitment_for(manifest_for(observations), batch.nonce, batch.schema_version)
    local_match = current == batch.commitment
    response = {"batch_id": batch.id, "local_commitment": current, "recorded_commitment": batch.commitment, "local_match": local_match, "chain_status": batch.receipt_status, "on_chain_match": False}
    if not batch.on_chain_commitment:
        response["chain_status"] = "pending_or_unavailable"
        return response
    try:
        web3, contract = _contract()
        on_chain = contract.functions.getBatch(_bytes32_text(web3, f"sentinel-batch:{batch.id}")).call()[0].hex().removeprefix("0x")
        response["on_chain_match"] = on_chain == batch.on_chain_commitment.removeprefix("0x") and local_match
        response["chain_status"] = "verified" if response["on_chain_match"] else "mismatch"
    except ChainUnavailable:
        response["chain_status"] = "chain_unavailable"
    return response


def anchor_research_grant(grant: ResearchGrant, researcher_address: str) -> ResearchGrant:
    """Anchor an approved synthetic aggregate grant; no patient data is included."""
    if not researcher_address:
        raise ChainUnavailable("CHAIN_RESEARCHER_ADDRESS is required to anchor a research grant.")
    web3, contract = _contract()
    account = web3.eth.account.from_key(get_settings().chain_private_key)
    grant_id, scope_hash, purpose_hash = _grant_identity(web3, grant)
    expiry = grant.expires_at.replace(tzinfo=timezone.utc) if grant.expires_at.tzinfo is None else grant.expires_at.astimezone(timezone.utc)
    tx = contract.functions.grantResearch(grant_id, web3.to_checksum_address(researcher_address), scope_hash, purpose_hash, int(expiry.timestamp())).build_transaction({"from": account.address, "nonce": web3.eth.get_transaction_count(account.address), "chainId": web3.eth.chain_id})
    _send_and_confirm(web3, account, tx)
    grant.chain_grant_hash = "0x" + grant_id.hex()
    return grant


def revoke_research_grant(grant: ResearchGrant) -> None:
    if not grant.chain_grant_hash:
        return
    web3, contract = _contract()
    account = web3.eth.account.from_key(get_settings().chain_private_key)
    grant_id, _, _ = _grant_identity(web3, grant)
    tx = contract.functions.revokeResearch(grant_id).build_transaction({"from": account.address, "nonce": web3.eth.get_transaction_count(account.address), "chainId": web3.eth.chain_id})
    _send_and_confirm(web3, account, tx)


def verify_research_grant(grant: ResearchGrant, researcher_address: str) -> bool:
    if not grant.chain_grant_hash or not researcher_address:
        return False
    web3, contract = _contract()
    grant_id, scope_hash, purpose_hash = _grant_identity(web3, grant)
    return bool(contract.functions.isGrantActive(grant_id, web3.to_checksum_address(researcher_address), scope_hash, purpose_hash).call())
