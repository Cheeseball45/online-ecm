"""
ecm.py — First-order Thevenin ECM simulation.

Circuit:  V_t = V_oc(SOC) - R0*I - V_rc
          V_rc[k+1] = V_rc[k]*exp(-dt/tau) + R1*(1-exp(-dt/tau))*I[k]
          SOC[k+1]  = SOC[k] - I[k]*dt / (Q_nom_ah * 3600)

All units: volts, amps, ohms, farads, seconds.
"""

import numpy as np
from scipy.interpolate import interp1d

# OCV curve for CALCE A1 LCO cell at -10C
# Derived from rest periods in A1-007 OCV test
_SOC_PTS = [0.0,   0.217,  0.954,  1.0]
_OCV_PTS = [2.208, 2.506,  3.402,  3.548]
_ocv_interp = interp1d(_SOC_PTS, _OCV_PTS, kind='linear', fill_value='extrapolate')

Q_NOM_AH = 1.35   # nominal capacity in Ah


def ocv(soc: float) -> float:
    """Return OCV in volts for a given SOC in [0, 1]."""
    return float(_ocv_interp(np.clip(soc, 0.0, 1.0)))


def step(
    I: float,
    V_rc: float,
    soc: float,
    R0: float,
    R1: float,
    C1: float,
    dt: float,
) -> tuple:
    """
    Advance ECM one timestep.

    Parameters
    ----------
    I     : current in amps (positive = discharge)
    V_rc  : RC voltage state at start of timestep
    soc   : state of charge in [0, 1]
    R0    : series resistance in ohms
    R1    : RC pair resistance in ohms
    C1    : RC pair capacitance in farads
    dt    : timestep in seconds

    Returns
    -------
    V_t_pred : predicted terminal voltage (V)
    V_rc_new : updated RC voltage state (V)
    soc_new  : updated SOC
    """
    V_oc = ocv(soc)
    tau  = max(R1 * C1, 1e-6)

    V_t_pred = V_oc - R0 * I - V_rc

    alpha   = np.exp(-dt / tau)
    V_rc_new = V_rc * alpha + R1 * (1.0 - alpha) * I

    soc_new = np.clip(soc - (I * dt) / (Q_NOM_AH * 3600.0), 0.0, 1.0)

    return float(V_t_pred), float(V_rc_new), float(soc_new)


def simulate_window(
    current_a: list,
    dt_s: list,
    soc_init: float,
    V_rc_init: float,
    R0: float,
    R1: float,
    C1: float,
) -> dict:
    """
    Simulate ECM over a window of samples.

    Parameters
    ----------
    current_a : list of current values (A)
    dt_s      : list of timesteps (s)
    soc_init  : SOC at start of window
    V_rc_init : RC voltage at start of window
    R0, R1, C1: ECM parameters

    Returns
    -------
    dict with keys:
        v_predicted : list of predicted voltages
        soc_final   : SOC at end of window
        v_rc_final  : RC voltage at end of window
    """
    n = len(current_a)
    v_predicted = []
    soc   = soc_init
    V_rc  = V_rc_init

    for k in range(n):
        V_t, V_rc, soc = step(current_a[k], V_rc, soc, R0, R1, C1, dt_s[k])
        v_predicted.append(V_t)

    return {
        'v_predicted': v_predicted,
        'soc_final':   soc,
        'v_rc_final':  V_rc,
    }
