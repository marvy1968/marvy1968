"""Simulation output shared by every sport."""

from dataclasses import dataclass

import numpy as np


@dataclass
class SimResult:
    home: np.ndarray  # final scores per simulation (including OT/shootout settlement)
    away: np.ndarray
    reg_tie: np.ndarray | None = None  # hockey/soccer: tied at the end of regulation

    @property
    def margin(self) -> np.ndarray:
        return self.home - self.away

    @property
    def total(self) -> np.ndarray:
        return self.home + self.away

    def home_win(self) -> float:
        return float((self.margin > 0).mean())

    def draw(self) -> float:
        return float((self.margin == 0).mean())
