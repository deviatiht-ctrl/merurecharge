"""Kalkil rechaj yo — tout bagay an Decimal, pa janm float."""
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

D = Decimal

# Presizyon aset la selon rezo a (Stellar: 7 desimal, TRC-20: 6)
PRECISION = {"stellar": 7, "trc20": 6}
Q_HTG = D("0.01")
Q_RATE = D("0.0001")


class CalcError(Exception):
    pass


def amount_str(v) -> str:
    """Fòma Decimal san eckspozan ni zewo anplis: 100 -> '100', 500.50 -> '500.5'.
    Enpòtan pou siyati retrè PLOP la (menm fòma nan siyati ak kò JSON)."""
    s = format(D(str(v)), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


@dataclass
class Quote:
    htg_paid: Decimal
    fee_pct: Decimal
    fee_model: str
    htg_net: Decimal
    taux_float: Decimal
    usdt_gross: Decimal
    fee_network: Decimal
    fee_meru: Decimal
    usdt_send: Decimal
    effective_rate: Decimal
    reference: Decimal
    loss_pct: Decimal
    loss_htg: Decimal
    within_loss_limit: bool

    def as_dict(self):
        return {
            "htg_paid": str(self.htg_paid),
            "fee_pct": str(self.fee_pct),
            "fee_model": self.fee_model,
            "htg_net": str(self.htg_net),
            "taux_float": str(self.taux_float),
            "usdt_gross": str(self.usdt_gross),
            "fee_network": str(self.fee_network),
            "fee_meru": str(self.fee_meru),
            "usdt_send": str(self.usdt_send),
            "effective_rate": str(self.effective_rate),
            "reference": str(self.reference),
            "loss_pct": str(self.loss_pct),
            "loss_htg": str(self.loss_htg),
            "within_loss_limit": self.within_loss_limit,
        }


def _floor(value: Decimal, precision: int) -> Decimal:
    return value.quantize(D(1).scaleb(-precision), rounding=ROUND_DOWN)


def compute_quote(
    amount_htg,
    taux_float,
    fee_pct=D("3"),
    fee_model="dedwi",
    fee_network=D("0"),
    fee_meru=D("0"),
    reference=D("129"),
    loss_limit_pct=D("4"),
    network="stellar",
) -> Quote:
    """
    HTG_peye -> HTG_net -> USDT_brit -> USDT_voye -> taux_efektif -> pèt.
    Rejte si valè antre yo envalid; 'within_loss_limit' endike si pèt a asepte.
    """
    htg_paid = D(str(amount_htg))
    taux_float = D(str(taux_float))
    fee_pct = D(str(fee_pct))
    fee_network = D(str(fee_network))
    fee_meru = D(str(fee_meru))
    reference = D(str(reference))
    loss_limit_pct = D(str(loss_limit_pct))

    if htg_paid <= 0:
        raise CalcError("Montan an dwe pi gran pase 0")
    if taux_float <= 0:
        raise CalcError("Taux float la pa defini (ranpli float la anvan)")
    if reference <= 0:
        raise CalcError("Taux referans lan envalid")
    if fee_model not in ("dedwi", "sou_tet"):
        raise CalcError("Modèl frè envalid")
    if fee_pct < 0 or fee_pct >= 100:
        raise CalcError("Frè PLOP envalid")

    if fee_model == "dedwi":
        htg_net = htg_paid * (D(1) - fee_pct / D(100))
    else:  # sou_tet: frè a chaje sou tèt montan an
        htg_net = htg_paid

    usdt_gross = htg_net / taux_float
    usdt_raw = usdt_gross - fee_network - fee_meru
    precision = PRECISION.get(network, 7)
    usdt_send = _floor(usdt_raw, precision)
    if usdt_send <= 0:
        raise CalcError("Kantite USDT a voye a twò piti apre frè yo")

    effective_rate = (htg_paid / usdt_send).quantize(Q_RATE)
    loss_pct = ((effective_rate / reference) - D(1)) * D(100)
    loss_pct = loss_pct.quantize(Q_RATE)
    loss_htg = (htg_paid - usdt_send * reference).quantize(Q_HTG)

    return Quote(
        htg_paid=htg_paid,
        fee_pct=fee_pct,
        fee_model=fee_model,
        htg_net=htg_net.quantize(Q_HTG),
        taux_float=taux_float.quantize(Q_RATE),
        usdt_gross=usdt_gross.quantize(D(1).scaleb(-precision)),
        fee_network=fee_network,
        fee_meru=fee_meru,
        usdt_send=usdt_send,
        effective_rate=effective_rate,
        reference=reference,
        loss_pct=loss_pct,
        loss_htg=loss_htg,
        within_loss_limit=loss_pct <= loss_limit_pct,
    )


def float_totals(refills, sent_usdt):
    """
    Kontablite float lan (pri kout mwayèn pondere):
      avg_rate = total HTG depanse / total USDT achte
      units    = total USDT achte - USDT voye
      cost     = units * avg_rate
    """
    total_htg = D(0)
    total_usdt = D(0)
    for r in refills:
        total_htg += D(str(r.htg_spent))
        total_usdt += D(str(r.usdt_received))
    units = total_usdt - D(str(sent_usdt))
    avg_rate = (total_htg / total_usdt) if total_usdt > 0 else None
    cost = (units * avg_rate) if (avg_rate is not None and units > 0) else D(0)
    return {
        "total_htg": total_htg,
        "total_usdt": total_usdt,
        "units": units,
        "avg_rate": avg_rate,
        "cost": cost,
    }
