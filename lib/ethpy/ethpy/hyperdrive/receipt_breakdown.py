"""A container for hyperdrive contract return values."""
from __future__ import annotations

from dataclasses import dataclass

from fixedpointmath import FixedPoint


@dataclass
class ReceiptBreakdown:
    r"""A granular breakdown of important values in a trade receipt."""
    asset_id: int = 0
    maturity_time_seconds: int = 0
    base_amount: FixedPoint = FixedPoint(0)
    bond_amount: FixedPoint = FixedPoint(0)
    lp_amount: FixedPoint = FixedPoint(0)
    withdrawal_share_amount: FixedPoint = FixedPoint(0)

    def __post_init__(self):
        if (
            self.base_amount < 0
            or self.bond_amount < 0
            or self.maturity_time_seconds < 0
            or self.lp_amount < 0
            or self.withdrawal_share_amount < 0
        ):
            raise ValueError(
                "All ReceiptBreakdown arguments must be positive,"
                " since they are expected to be unsigned integer values from smart contracts."
            )
