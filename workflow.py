"""
workflow.py — OnlineEcmRun Temporal workflow.

IMPORTANT: This file must be deterministic.
    - No numpy, scipy, psycopg2, or random calls here
    - No direct DB access
    - All computation happens in activities.py
    - State (theta, P, v_rc, soc) is carried as plain Python types
"""

from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from activities import load_window, simulate, reestimate, persist


ACTIVITY_TIMEOUT = timedelta(seconds=60)
CONTINUE_AS_NEW_EVERY = 200   # windows before continue-as-new to bound history


@workflow.defn
class OnlineEcmRun:
    """
    Online ECM run workflow.

    Loops over windows of test data, simulating the ECM and periodically
    re-estimating parameters via RLS. Persists predictions and params to BattDB.

    cfg keys:
        test_id         : int   — battdb test_id
        test_name       : str   — human readable
        window_size     : int   — samples per window (default 100)
        reestimate_every: int   — re-run RLS every N windows (default 10)
        lam             : float — RLS forgetting factor (default 0.98)
        total_rows      : int   — total samples in test
        start_offset    : int   — row offset to start from (for continue-as-new)
        window_index    : int   — global window counter (for continue-as-new)
        init_state      : dict  — initial theta, P, v_rc, soc, alpha, dt_nom
    """

    @workflow.run
    async def run(self, cfg: dict) -> dict:
        test_id          = cfg['test_id']
        window_size      = cfg.get('window_size', 100)
        reestimate_every = cfg.get('reestimate_every', 10)
        lam              = cfg.get('lam', 0.98)
        total_rows       = cfg['total_rows']
        offset           = cfg.get('start_offset', 0)
        window_index     = cfg.get('window_index', 0)
        state            = cfg['init_state']

        opts = {'start_to_close_timeout': ACTIVITY_TIMEOUT}

        loop_count = 0

        while offset < total_rows:
            # ── 1. Load window ────────────────────────────────────────────
            window = await workflow.execute_activity(
                load_window,
                args=[test_id, offset, window_size],
                **opts,
            )

            if window['n_rows'] == 0:
                break

            # ── 2. Simulate with current parameters ───────────────────────
            sim = await workflow.execute_activity(
                simulate,
                args=[window, state],
                **opts,
            )

            # Update SOC and v_rc from simulation
            state = {**state, 'soc': sim['soc_final'], 'v_rc': sim['v_rc_final']}

            # ── 3. Re-estimate parameters every K windows ─────────────────
            if window_index % reestimate_every == 0:
                state = await workflow.execute_activity(
                    reestimate,
                    args=[window, state, lam],
                    **opts,
                )

            # ── 4. Persist predictions and params ─────────────────────────
            await workflow.execute_activity(
                persist,
                args=[test_id, window_index, sim, state],
                **opts,
            )

            offset       += window['n_rows']
            window_index += 1
            loop_count   += 1

            # ── 5. continue-as-new to keep history bounded ─────────────────
            if loop_count % CONTINUE_AS_NEW_EVERY == 0 and offset < total_rows:
                return await workflow.continue_as_new({
                    **cfg,
                    'start_offset': offset,
                    'window_index': window_index,
                    'init_state':   state,
                })

        return {
            'final_state':   state,
            'windows_run':   window_index,
            'rows_processed': offset,
        }
