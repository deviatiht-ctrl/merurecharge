"""Tès calc.py — tout fòmil an Decimal."""
from decimal import Decimal

import pytest

from app.calc import CalcError, compute_quote, float_totals


def test_dedwi_3pct():
    # 500 HTG, taux 130, frè 3% dedwi -> net 485 -> 3.7307692 USDT (7 dec, anba)
    q = compute_quote(500, 130, fee_pct=3, fee_model="dedwi")
    assert q.htg_net == Decimal("485.00")
    assert q.usdt_send == Decimal("3.7307692")
    assert q.effective_rate == (Decimal(500) / q.usdt_send).quantize(Decimal("0.0001"))


def test_sou_tet_3pct():
    # sou tèt: montan an pa redwi — net = 500
    q = compute_quote(500, 130, fee_pct=3, fee_model="sou_tet")
    assert q.htg_net == Decimal("500.00")
    assert q.usdt_send == Decimal("3.8461538")
    assert q.usdt_send > compute_quote(500, 130).usdt_send


def test_arrondi_anba_stellar_7dec():
    q = compute_quote(500, 130, network="stellar")
    raw = Decimal("485") / Decimal("130")  # 3.7307692307...
    assert q.usdt_send == raw.quantize(Decimal("0.0000001"))
    assert q.usdt_send <= raw  # toujou anba


def test_arrondi_trc20_6dec():
    q = compute_quote(500, 130, network="trc20")
    raw = Decimal("485") / Decimal("130")
    assert q.usdt_send == raw.quantize(Decimal("0.000001"))


def test_fre_rezo_ak_meru():
    q = compute_quote(500, 130, fee_network=Decimal("0.1"), fee_meru=Decimal("0.05"))
    raw = Decimal("485") / Decimal("130") - Decimal("0.15")
    assert q.usdt_send == raw.quantize(Decimal("0.0000001"))


def test_referans_129():
    # taux efektif pi wo pase 129 -> pèt pozitif
    q = compute_quote(500, 130, reference=129)
    assert q.loss_pct > 0
    assert q.loss_htg > 0


def test_referans_141():
    # taux efektif pi ba pase 141 -> "pèt" negatif (ganyen)
    q = compute_quote(500, 130, reference=141)
    assert q.loss_pct < 0
    assert q.loss_htg < 0


def test_limit_pet():
    q = compute_quote(500, 140, reference=129, loss_limit_pct=4)
    assert not q.within_loss_limit
    q2 = compute_quote(500, 130, reference=129, loss_limit_pct=4)
    assert q2.within_loss_limit


def test_divizyon_pa_zewo():
    with pytest.raises(CalcError):
        compute_quote(500, 0)


def test_montan_negatif():
    with pytest.raises(CalcError):
        compute_quote(-10, 130)


def test_kantite_ti_apre_fre():
    with pytest.raises(CalcError):
        compute_quote(20, 130, fee_network=Decimal("5"))


def test_decimal_pa_float():
    # Presizyon: 1000 HTG @ 137 — verifye pa gen erè float, arondi ANBA
    from decimal import ROUND_DOWN
    q = compute_quote(1000, 137)
    net = Decimal(1000) * Decimal("0.97")
    assert q.usdt_send == (net / Decimal(137)).quantize(
        Decimal("0.0000001"), rounding=ROUND_DOWN)
    assert isinstance(q.usdt_send, Decimal)


def test_float_totals_weighted_avg():
    class R:
        def __init__(self, h, u):
            self.htg_spent, self.usdt_received = str(h), str(u)

    # 2 ranpli: 100 USDT @130 + 100 USDT @140 -> avg 135
    t = float_totals([R(13000, 100), R(14000, 100)], 0)
    assert t["avg_rate"] == Decimal("135")
    assert t["units"] == Decimal("200")
    # apre 50 USDT voye: units 150, kòst = 150*135
    t2 = float_totals([R(13000, 100), R(14000, 100)], 50)
    assert t2["units"] == Decimal("150")
    assert t2["cost"] == Decimal("20250")
