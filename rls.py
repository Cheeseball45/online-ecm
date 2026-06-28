"""
rls.py — Recursive Least Squares with forgetting factor for ECM identification.

Identifies a 1RC Thevenin ECM online, sample by sample.

Model (discrete, fixed alpha = exp(-dt/tau)):
    y[k]   = V_t[k] - V_oc[k] + alpha*(V_oc[k-1] - V_t[k-1])
    phi[k] = [-I[k], I[k-1]]
    theta  = [a, b]   where a = R0, b = alpha*R0 - R1*(1-alpha)

Parameter recovery:
    R0 = theta[0]
    R1 = (alpha*theta[0] - theta[1]) / (1 - alpha)
    C1 = tau / R1   where tau = -dt / ln(alpha)

Deliberately dependency-light — pure numpy arithmetic on small arrays.
Easy to unit-test and straightforward to port to C.
"""

import numpy as np


class RLS:
    def __init__(
        self,
        lam: float = 0.995,
        alpha: float = 0.97,
        dt: float = 3.0,
        theta_init: list = None,
        P_init: float = 1000.0,
    ):
        """
        Parameters
        ----------
        lam : float
            Forgetting factor (0 < lambda <= 1).
            0.99 = ~100 samples of memory, 0.999 = ~1000 samples.
        alpha : float
            RC discretisation factor exp(-dt/tau). Fixed during estimation.
            Controls how fast the RC voltage decays between samples.
        dt : float
            Sample timestep in seconds (approximate, updated each step).
        theta_init : list[float]
            Initial parameter guess [a, b]. Defaults to [R0=0.1, b=0.0].
        P_init : float
            Initial covariance diagonal value. Large = high uncertainty.
        """
        self.lam   = lam
        self.alpha = alpha
        self.dt    = dt

        self.theta = np.array(theta_init if theta_init else [0.1, 0.0], dtype=float)
        self.P     = np.eye(2) * P_init

        # State memory
        self.I_prev    = 0.0
        self.V_t_prev  = None
        self.V_oc_prev = None

        # Covariance windup guard
        self.P_max = 1e6

        # History for plotting
        self.history = {
            'k':     [],
            'R0':    [],
            'R1':    [],
            'C1':    [],
            'tau':   [],
            'y':     [],
            'y_hat': [],
            'error': [],
        }

    def update(self, I: float, V_t: float, V_oc: float, dt: float = None) -> dict:
        """
        Process one sample. Returns recovered physical parameters.

        Parameters
        ----------
        I   : float  Current in amps (positive = discharge convention)
        V_t : float  Measured terminal voltage in volts
        V_oc: float  Open circuit voltage estimate at current SOC in volts
        dt  : float  Timestep since last sample in seconds (optional override)

        Returns
        -------
        dict with keys: R0, R1, C1, tau, y, y_hat, error
        """
        if dt is not None:
            self.dt = dt

        # First sample — initialise memory, no update yet
        if self.V_t_prev is None:
            self.V_t_prev  = V_t
            self.V_oc_prev = V_oc
            self.I_prev    = I
            return self._recover()

        # ── Regression vector and output ──────────────────────────────────
        phi = np.array([-I, self.I_prev])
        y   = (V_t - V_oc) + self.alpha * (self.V_oc_prev - self.V_t_prev)

        # ── RLS update ────────────────────────────────────────────────────
        denom = self.lam + phi @ self.P @ phi
        K     = (self.P @ phi) / denom

        y_hat         = phi @ self.theta
        innovation    = y - y_hat

        self.theta    = self.theta + K * innovation
        self.P        = (np.eye(2) - np.outer(K, phi)) @ self.P / self.lam

        # Covariance windup guard — cap diagonal
        self.P = np.clip(self.P, -self.P_max, self.P_max)

        # ── Update memory ─────────────────────────────────────────────────
        self.V_t_prev  = V_t
        self.V_oc_prev = V_oc
        self.I_prev    = I

        result = self._recover()
        result.update({'y': y, 'y_hat': y_hat, 'error': innovation})
        return result

    def _recover(self) -> dict:
        """Convert theta [a, b] back to physical parameters R0, R1, C1."""
        a, b = self.theta
        R0 = float(a)

        alpha = self.alpha
        denom_r1 = (1.0 - alpha)
        if abs(denom_r1) < 1e-9:
            R1 = 0.0
        else:
            R1 = float((alpha * a - b) / denom_r1)

        # Clip to physically reasonable range
        R0 = np.clip(R0, 0.0, 5.0)
        R1 = np.clip(R1, 0.0, 5.0)

        tau = float(-self.dt / np.log(max(alpha, 1e-9)))
        C1  = float(tau / max(R1, 1e-6))

        return {'R0': R0, 'R1': R1, 'C1': C1, 'tau': tau}

    def reset_covariance(self, P_init: float = 1000.0):
        """Reset P — useful if parameters have been constant for a long time."""
        self.P = np.eye(2) * P_init
