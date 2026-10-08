"""Kliyan wallet reyèl — USDC sou Stellar oswa Polygon, USDT TRC-20 sou TRON.

Enpòt yo fèt lazily pou mòd mock la pa bezwen stellar-sdk/tronpy/web3 enstale.
Polygon se rezo a ki pi senp: pa gen aktivasyon, pa gen trustline,
pa gen rezèv minimòm — sèlman ti POL pou gas (~$0.005/tx).
"""
from datetime import datetime
from decimal import Decimal

from .. import config


class WalletError(Exception):
    pass


def _dec(v) -> Decimal:
    return Decimal(str(v))


class StellarWallet:
    """USDC sou Stellar. Bezwen yon ti XLM + trustline USDC."""

    def __init__(self, secret: str, issuer: str, horizon_url: str):
        try:
            from stellar_sdk import Asset, Keypair, Server
        except ImportError:
            raise WalletError("stellar-sdk pa enstale (pip install stellar-sdk)")
        if not secret:
            raise WalletError("WALLET_SECRET pa konfigire")
        self._Server = Server
        self._Asset = Asset
        self.kp = Keypair.from_secret(secret)
        self.asset = Asset("USDC", issuer)
        self.server = Server(horizon_url)
        self.address = self.kp.public_key

    def get_balance(self) -> Decimal:
        try:
            acc = self.server.accounts().account_id(self.address).call()
        except Exception as e:
            raise WalletError(f"Horizon: {e}")
        for b in acc.get("balances", []):
            if (b.get("asset_code") == "USDC"
                    and b.get("asset_issuer") == self.asset.issuer):
                return _dec(b["balance"])
        return Decimal(0)

    def send(self, amount: Decimal, address: str, memo: str | None = None) -> str:
        try:
            from stellar_sdk import Memo, Network, TransactionBuilder
        except ImportError:
            raise WalletError("stellar-sdk pa enstale")
        try:
            src = self.server.load_account(self.address)
            tb = (TransactionBuilder(src, Network.PUBLIC_NETWORK_PASSPHRASE,
                                     base_fee=100)
                  .append_payment_op(address, self.asset, str(amount))
                  .set_timeout(60))
            if memo:
                tb.add_memo(Memo.text(memo))
            tx = tb.build()
            tx.sign(self.kp)
            resp = self.server.submit_transaction(tx)
            return resp["hash"]
        except Exception as e:
            raise WalletError(f"Stellar send echwe: {e}")

    def tx_status(self, tx_hash: str) -> str:
        try:
            self.server.transactions().transaction(tx_hash).call()
            return "confirmed"
        except Exception as e:
            if "404" in str(e) or "Not Found" in str(e):
                return "pending"
            return "pending"

    def find_outgoing(self, address: str, amount: Decimal, memo: str | None,
                      since: datetime) -> str | None:
        try:
            pays = (self.server.payments().for_account(self.address)
                    .order(desc=True).limit(50).call())
            for p in pays.get("_embedded", {}).get("records", []):
                if p.get("type") != "payment" or p.get("from") != self.address:
                    continue
                if p.get("to") != address:
                    continue
                if p.get("asset_code") != "USDC":
                    continue
                if abs(_dec(p.get("amount", 0)) - amount) > Decimal("0.0000001"):
                    continue
                if p.get("created_at", "") < since.strftime("%Y-%m-%dT%H:%M:%S"):
                    continue
                return p.get("transaction_hash")
        except Exception:
            pass
        return None

    def network_fee(self) -> Decimal:
        return Decimal("0.00001")


class TronWallet:
    """USDT TRC-20 sou TRON. Bezwen TRX pou frè."""

    CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # USDT TRC-20

    def __init__(self, secret: str, api_key: str = ""):
        try:
            from tronpy import Tron
            from tronpy.keys import PrivateKey
        except ImportError:
            raise WalletError("tronpy pa enstale (pip install tronpy)")
        if not secret:
            raise WalletError("WALLET_SECRET pa konfigire")
        self._Tron = Tron
        self.priv = PrivateKey(bytes.fromhex(secret.replace("0x", "")))
        self.client = Tron(api_key=api_key or None)
        self.address = self.priv.public_key.to_base58check_address()
        self.contract = self.client.get_contract(self.CONTRACT)

    def get_balance(self) -> Decimal:
        try:
            raw = self.contract.functions.balanceOf(self.address)
            return _dec(raw) / Decimal(10) ** 6
        except Exception as e:
            raise WalletError(f"TronGrid: {e}")

    def send(self, amount: Decimal, address: str, memo: str | None = None) -> str:
        try:
            sun = int(amount * Decimal(10) ** 6)
            txn = (self.contract.functions.transfer(address, sun)
                   .with_owner(self.address)
                   .fee_limit(20_000_000)
                   .build()
                   .sign(self.priv))
            result = txn.broadcast().wait()
            return txn.txid
        except Exception as e:
            raise WalletError(f"TRON send echwe: {e}")

    def tx_status(self, tx_hash: str) -> str:
        try:
            info = self.client.get_transaction_info(tx_hash)
            if not info:
                return "pending"
            return "confirmed" if info.get("receipt", {}).get("result", "SUCCESS") == "SUCCESS" else "failed"
        except Exception:
            return "pending"

    def find_outgoing(self, address: str, amount: Decimal, memo: str | None,
                      since: datetime) -> str | None:
        return None  # pa sipòte — worker a ap reeseye san confirmasyon on-chain

    def network_fee(self) -> Decimal:
        return Decimal("1")


class PolygonWallet:
    """USDC (ERC-20) sou Polygon. Pa gen aktivasyon — adrès la egziste nèt.
    Sèl bezwen: ti POL pou gas (~0.005 pa tranzaksyon)."""

    CHAIN_ID = 137
    TRANSFER_SIG = ("0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
    ERC20_ABI = [
        {"name": "balanceOf", "type": "function", "stateMutability": "view",
         "inputs": [{"name": "account", "type": "address"}],
         "outputs": [{"name": "", "type": "uint256"}]},
        {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
         "inputs": [{"name": "to", "type": "address"},
                    {"name": "amount", "type": "uint256"}],
         "outputs": [{"name": "", "type": "bool"}]},
    ]

    def __init__(self, secret: str, rpc_url: str | None = None):
        try:
            from web3 import Web3
        except ImportError:
            raise WalletError("web3 pa enstale (pip install web3)")
        if not secret:
            raise WalletError("WALLET_SECRET pa konfigire")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url or config.POLYGON_RPC,
                                         request_kwargs={"timeout": 20}))
        key = secret if secret.startswith("0x") else "0x" + secret
        self.acct = self.w3.eth.account.from_key(key)
        self.address = self.acct.address
        self.usdc = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.POLYGON_USDC_CONTRACT),
            abi=self.ERC20_ABI)

    def _units(self, amount) -> int:
        return int(_dec(amount) * Decimal(10) ** 6)

    def get_balance(self) -> Decimal:
        try:
            raw = self.usdc.functions.balanceOf(self.address).call()
            return _dec(raw) / Decimal(10) ** 6
        except Exception as e:
            raise WalletError(f"Polygon RPC: {e}")

    def send(self, amount: Decimal, address: str, memo: str | None = None) -> str:
        try:
            tx = self.usdc.functions.transfer(
                self.w3.to_checksum_address(address), self._units(amount)
            ).build_transaction({
                "from": self.address,
                "nonce": self.w3.eth.get_transaction_count(self.address),
                "chainId": self.CHAIN_ID,
            })
            signed = self.w3.eth.account.sign_transaction(tx, self.acct.key)
            txh = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            return txh.hex()
        except Exception as e:
            raise WalletError(f"Polygon send echwe: {e}")

    def tx_status(self, tx_hash: str) -> str:
        try:
            r = self.w3.eth.get_transaction_receipt(tx_hash)
            if r is None:
                return "pending"
            return "confirmed" if r["status"] == 1 else "failed"
        except Exception:
            return "pending"

    def find_outgoing(self, address: str, amount: Decimal, memo: str | None,
                      since: datetime) -> str | None:
        """Jwenn Transfer USDC soti nan float la -> adrès la (idempotans)."""
        try:
            pad = lambda a: "0x" + a.lower().replace("0x", "").rjust(64, "0")
            logs = self.w3.eth.get_logs({
                "address": self.usdc.address,
                "topics": [self.TRANSFER_SIG, pad(self.address), pad(address)],
                "fromBlock": max(0, self.w3.eth.block_number - 100_000),
                "toBlock": "latest",
            })
            for l in reversed(logs):
                if int(l["data"], 16) == self._units(amount):
                    return l["transactionHash"].hex()
        except Exception:
            pass
        return None

    def network_fee(self) -> Decimal:
        return Decimal("0.01")


def get_real_wallet(asset: str | None = None, network: str | None = None):
    asset = (asset or config.WALLET_ASSET).upper()
    network = (network or config.WALLET_NETWORK).lower()
    if network == "stellar":
        return StellarWallet(config.WALLET_SECRET, config.USDC_ISSUER,
                             config.HORIZON_URL)
    if network == "trc20":
        return TronWallet(config.WALLET_SECRET, config.TRONGRID_API_KEY)
    if network == "polygon":
        return PolygonWallet(config.WALLET_SECRET, config.POLYGON_RPC)
    raise WalletError(f"Rezo '{network}' pa sipòte")
