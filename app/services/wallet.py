"""Kliyan wallet reyèl — USDC sou Stellar (default) oswa USDT TRC-20 sou TRON.

Enpòt yo fèt lazily pou mòd mock la pa bezwen stellar-sdk/tronpy enstale.
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


def get_real_wallet(asset: str | None = None, network: str | None = None):
    asset = (asset or config.WALLET_ASSET).upper()
    network = (network or config.WALLET_NETWORK).lower()
    if network == "stellar":
        return StellarWallet(config.WALLET_SECRET, config.USDC_ISSUER,
                             config.HORIZON_URL)
    if network == "trc20":
        return TronWallet(config.WALLET_SECRET, config.TRONGRID_API_KEY)
    raise WalletError(f"Rezo '{network}' pa sipòte")
