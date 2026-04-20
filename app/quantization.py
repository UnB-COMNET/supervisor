"""KPI quantization and intent drift"""

import math
from dataclasses import dataclass, field

# Operational baseline used as target vector for drift calculation
KPI_TARGETS = {
    "delay_ms": 40.0,
    "throughput_mbps": 50.0,
}

_P3_LABEL = {1: "Normal", 0: "Warning", -1: "Critical"}


def quantize_9ary(kpi_name: str, value: float) -> int:
    """Maps a continuous KPI value to P9 in {0, -1, -2, -3, -4}

    Both KPIs here are one-sided: delay has no 'too low' issue,
    throughput has no 'too high' issue — so P9 only goes negative.
    """
    if kpi_name == "delay_ms":
        # lower is better; critical boundary at 100 ms
        if value <= 40:    return 0
        elif value <= 60:  return -1
        elif value <= 100: return -2
        elif value <= 120: return -3
        else:              return -4
    if kpi_name == "throughput_mbps":
        # higher is better; critical boundary at 25 Mbps
        if value >= 50:   return 0
        elif value >= 37: return -1
        elif value >= 25: return -2
        elif value >= 12: return -3
        else:             return -4
    raise ValueError(f"Unknown KPI: {kpi_name}")


def quantize_3ary(kpi_name: str, value: float) -> int:
    """Maps P9 → P3: 0 → Normal(1), {-1,-2} → Warning(0), {-3,-4} → Critical(-1)."""
    p9 = quantize_9ary(kpi_name, value)
    if p9 == 0:
        return 1
    if p9 in (-1, -2):
        return 0
    return -1


@dataclass
class DriftReport:
    kpi_states: dict = field(default_factory=dict) # {kpi: (p9, p3, label)}
    health: int = 1
    health_label: str = "Normal"
    delta: dict = field(default_factory=dict) # {kpi: operational - target}
    distance: float = 0.0 # ΔK
    gradient: dict = field(default_factory=dict) # {kpi: (2/n) * δi}


def compute_drift(operational: dict, targets: dict = None) -> DriftReport:
    """Quantizes KPIs, computes Kleene health, drift distance and gradient vector."""
    if targets is None:
        targets = KPI_TARGETS

    kpi_states = {
        k: (quantize_9ary(k, v), quantize_3ary(k, v), _P3_LABEL[quantize_3ary(k, v)])
        for k, v in operational.items()
    }
    health = min(p3 for _, p3, _ in kpi_states.values())

    n = len(operational)
    delta = {k: operational[k] - targets[k] for k in operational}
    distance = math.sqrt(sum(d ** 2 for d in delta.values()))
    gradient = {k: (2 / n) * d for k, d in delta.items()}

    return DriftReport(
        kpi_states=kpi_states,
        health=health,
        health_label=_P3_LABEL[health],
        delta=delta,
        distance=distance,
        gradient=gradient,
    )
