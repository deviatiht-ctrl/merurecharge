"""Tès wallet Polygon — derivasyon + dispatch (san rele RPC la)."""
import pytest

from app.services.wallet import PolygonWallet, get_real_wallet, WalletError


def test_polygon_address_derivation():
    """Vektè koni: kle 0x11*32 -> adrès 0x19E7E376..."""
    w = PolygonWallet("0x" + "11" * 32)
    assert w.address.lower() == "0x19e7e376e7c213b7e7e7e46cc70a5dd086daff2a"


def test_polygon_no_secret():
    from app import config
    old = config.WALLET_SECRET
    config.WALLET_SECRET = ""
    try:
        with pytest.raises(WalletError):
            PolygonWallet("")
    finally:
        config.WALLET_SECRET = old


def test_dispatch_polygon(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "WALLET_SECRET", "0x" + "11" * 32)
    w = get_real_wallet("USDC", "polygon")
    assert isinstance(w, PolygonWallet)


def test_dispatch_bad_network():
    with pytest.raises(WalletError):
        get_real_wallet("USDC", "bitcoin")
