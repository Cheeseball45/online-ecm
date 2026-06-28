"""
activities.py — Temporal activities for the OnlineEcmRun workflow.

Four activities:
    load_window   — read N samples from BattDB
    simulate      — run ECM forward with current parameters
    reestimate    — run RLS on the window, return updated theta/P
    persist       — write predictions and params to BattDB

Rules:
    - All numpy/scipy/psycopg2 lives here, NOT in workflow.py
    - State (theta, P, V_rc, SOC) passes in and out as plain lists/floats
    - Activities must be idempotent — Temporal may retry them
"""

import math
import numpy as np
from datetime import timedelta
from temporalio import activity

import db
import ecm
from rls import RLS


# ── load_window ────────────────────────────────────────────────────────────

@activity.defn
async def load_window(test_id: int, offset: int, window_size: int) -> dict:
    """Read a window of samples from BattDB. Returns plain Python dict."""
    activity.logger.info(f"load_window: test_id={test_id} offset={offset} size={window_size}")
    window = db.load_window(test_id, offset, window_size)
    activity.logger.info(f"load_window: got {window['n_rows']} rows")
    return window


# ── simulate ───────────────────────────────────────────────────────────────

@activity.defn
async def simulate(window: dict, state: dict) -> dict:
    """
    Run ECM forward over the window using current parameters.

    Parameters
    ----------
    window : dict from load_window
    state  : dict with keys: theta (list[2]), P (list[4]),
             v_rc (float), soc (float)

    Returns
    -------
    dict with keys:
        v_predicted  : list[float]
        v_measured   : list[float]
        recorded_at  : list[str]
        window_rmse  : float
        soc_final    : float
        v_rc_final   : float
    """
    theta = state['theta']
    R0    = float(theta[0])

    # Recover R1, C1 from theta
    alpha = state.get('alpha', 0.97)
    b     = float(theta[1])
    denom = (1.0 - alpha)
    R1    = float((alpha * R0 - b) / denom) if abs(denom) > 1e-9 else 0.08
    R1    = max(min(R1, 5.0), 0.0)
    dt_nom = state.get('dt_nom', 3.0)
    tau   = -dt_nom / math.log(max(alpha, 1e-9))
    C1    = tau / max(R1, 1e-6)

    sim = ecm.simulate_window(
        current_a  = window['current_a'],
        dt_s       = window['dt_s'],
        soc_init   = state['soc'],
        V_rc_init  = state['v_rc'],
        R0=R0, R1=R1, C1=C1,
    )

    v_pred = sim['v_predicted']
    v_meas = window['voltage_v']
    n      = min(len(v_pred), len(v_meas))

    errors = [(v_pred[i] - v_meas[i]) * 1000 for i in range(n)]
    window_rmse = math.sqrt(sum(e**2 for e in errors) / n) if n > 0 else 0.0

    activity.logger.info(f"simulate: window RMSE={window_rmse:.2f} mV  R0={R0:.4f} R1={R1:.4f}")

    return {
        'v_predicted':  v_pred[:n],
        'v_measured':   v_meas[:n],
        'recorded_at':  window['recorded_at'][:n],
        'window_rmse':  window_rmse,
        'soc_final':    sim['soc_final'],
        'v_rc_final':   sim['v_rc_final'],
    }


# ── reestimate ─────────────────────────────────────────────────────────────

@activity.defn
async def reestimate(window: dict, state: dict, lam: float) -> dict:
    """
    Run RLS over the window. Returns updated state with new theta and P.

    Parameters
    ----------
    window : dict from load_window
    state  : current workflow state
    lam    : forgetting factor

    Returns
    -------
    Updated state dict (theta, P, v_rc, soc, alpha, dt_nom)
    """
    alpha  = state.get('alpha', 0.97)
    dt_nom = state.get('dt_nom', 3.0)

    # Reconstruct RLS from carried state
    estimator = RLS(
        lam=lam,
        alpha=alpha,
        dt=dt_nom,
        theta_init=state['theta'],
        P_init=1.0,   # P is restored below
    )
    # Restore covariance matrix from flat list
    P_flat = state.get('P', [1000.0, 0.0, 0.0, 1000.0])
    estimator.P = np.array(P_flat).reshape(2, 2)

    current_a = window['current_a']
    voltage_v = window['voltage_v']
    dt_s      = window['dt_s']

    soc   = state['soc']
    v_rc  = state['v_rc']

    # Feed window through RLS
    for k in range(len(current_a)):
        I   = current_a[k]
        V_t = voltage_v[k]
        dt  = dt_s[k]

        # OCV estimate
        V_oc = ecm.ocv(soc)

        params = estimator.update(I, V_t, V_oc, dt=dt)

        # Update SOC and v_rc
        R0, R1, C1 = params['R0'], params['R1'], params['C1']
        tau   = max(R1 * C1, 1.0)
        a     = math.exp(-dt / tau)
        v_rc  = v_rc * a + R1 * (1 - a) * I
        soc   = max(min(soc - I * dt / (1.35 * 3600), 1.0), 0.0)

    new_theta = estimator.theta.tolist()
    new_P     = estimator.P.flatten().tolist()

    activity.logger.info(
        f"reestimate: θ={new_theta}  "
        f"R0={estimator._recover()['R0']:.4f}  "
        f"R1={estimator._recover()['R1']:.4f}"
    )

    return {
        'theta':  new_theta,
        'P':      new_P,
        'v_rc':   v_rc,
        'soc':    soc,
        'alpha':  alpha,
        'dt_nom': dt_nom,
    }


# ── persist ────────────────────────────────────────────────────────────────

@activity.defn
async def persist(test_id: int, window_index: int, sim: dict, state: dict) -> None:
    """
    Write predictions and parameter snapshot to BattDB.

    Parameters
    ----------
    test_id      : int
    window_index : int
    sim          : dict from simulate activity
    state        : current workflow state
    """
    activity.logger.info(f"persist: window={window_index} RMSE={sim['window_rmse']:.2f}mV")

    # Build prediction records
    n = len(sim['v_predicted'])
    records = [
        {
            'recorded_at': sim['recorded_at'][i] if sim['recorded_at'][i] else '',
            'v_measured':  sim['v_measured'][i],
            'v_predicted': sim['v_predicted'][i],
            'abs_error':   abs(sim['v_predicted'][i] - sim['v_measured'][i]),
        }
        for i in range(n)
    ]

    db.persist_predictions(test_id, window_index, records)

    # Recover physical params for logging
    alpha  = state.get('alpha', 0.97)
    dt_nom = state.get('dt_nom', 3.0)
    theta  = state['theta']
    R0     = float(theta[0])
    b      = float(theta[1])
    denom  = 1.0 - alpha
    R1     = float((alpha * R0 - b) / denom) if abs(denom) > 1e-9 else 0.08
    R1     = max(min(R1, 5.0), 0.0)
    tau    = -dt_nom / math.log(max(alpha, 1e-9))
    C1     = tau / max(R1, 1e-6)

    # Use last recorded_at from window or current time
    recorded_at = sim['recorded_at'][-1] if sim['recorded_at'] else ''

    db.persist_params(test_id, window_index, {
        'recorded_at': recorded_at,
        'r0':          R0,
        'r1':          R1,
        'c1':          C1,
        'soc':         state['soc'],
        'window_rmse': sim['window_rmse'],
    })
