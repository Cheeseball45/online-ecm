"""
db.py — BattDB read and write helpers for the online ECM worker.

All functions take a connection string and return plain Python types
(lists, dicts, floats) — no numpy or pandas here so activities stay
serialisable by Temporal.
"""

import os
import psycopg2
from psycopg2.extras import execute_values


def _db_config():
    return {
        'host':     os.environ.get('DB_HOST',     'localhost'),
        'port':     int(os.environ.get('DB_PORT', '5454')),
        'dbname':   os.environ.get('DB_NAME',     'battdb'),
        'user':     os.environ.get('DB_USER',     'postgres'),
        'password': os.environ.get('DB_PASSWORD', 'password'),
    }


def get_connection():
    return psycopg2.connect(**_db_config())


def load_window(test_id: int, offset: int, window_size: int) -> dict:
    """
    Read a window of test_data rows from battdb.

    Parameters
    ----------
    test_id     : integer test_id from test_meta
    offset      : row offset (0-based)
    window_size : number of rows to read

    Returns
    -------
    dict with keys:
        current_a   : list[float]
        voltage_v   : list[float]
        dt_s        : list[float]
        recorded_at : list[str]   ISO timestamps
        n_rows      : int
    """
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute("""
        SELECT
            current_ma / 1000.0,
            voltage_mv / 1000.0,
            test_time_s,
            recorded_datetime
        FROM test_data
        WHERE test_id = %s
        ORDER BY test_time_s
        LIMIT %s OFFSET %s
    """, (test_id, window_size, offset))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return {'current_a': [], 'voltage_v': [], 'dt_s': [], 'recorded_at': [], 'n_rows': 0}

    current_a   = [float(r[0]) for r in rows]
    voltage_v   = [float(r[1]) for r in rows]
    test_time_s = [float(r[2]) for r in rows]
    recorded_at = [str(r[3]) if r[3] else '' for r in rows]

    # Compute dt from test_time_s
    dt_s = [3.0] * len(rows)   # default
    for i in range(1, len(rows)):
        dt = test_time_s[i] - test_time_s[i - 1]
        dt_s[i] = max(min(dt, 60.0), 0.1)

    return {
        'current_a':   current_a,
        'voltage_v':   voltage_v,
        'dt_s':        dt_s,
        'recorded_at': recorded_at,
        'n_rows':      len(rows),
    }


def get_test_row_count(test_id: int) -> int:
    """Return total number of rows in test_data for this test."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM test_data WHERE test_id = %s", (test_id,))
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return int(count)


def get_test_id(test_name: str) -> int:
    """Look up test_id by test_name."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("SELECT test_id FROM test_meta WHERE test_name = %s", (test_name,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise ValueError(f"Test not found: {test_name}")
    return int(row[0])


def persist_predictions(test_id: int, window_index: int, records: list):
    """
    Write prediction records to ecm_predictions table.

    Parameters
    ----------
    test_id      : int
    window_index : int
    records      : list of dicts with keys:
                   recorded_at, v_measured, v_predicted, abs_error
    """
    if not records:
        return

    conn = get_connection()
    cur  = conn.cursor()

    rows = [
        (
            test_id,
            r['recorded_at'],
            window_index,
            r['v_measured'],
            r['v_predicted'],
            r['abs_error'],
        )
        for r in records
    ]

    execute_values(cur, """
        INSERT INTO ecm_predictions
            (test_id, recorded_at, window_index, v_measured, v_predicted, abs_error)
        VALUES %s
        ON CONFLICT DO NOTHING
    """, rows)

    conn.commit()
    cur.close()
    conn.close()


def persist_params(test_id: int, window_index: int, params: dict):
    """
    Write one parameter snapshot to ecm_params table.

    Parameters
    ----------
    test_id      : int
    window_index : int
    params       : dict with keys: recorded_at, r0, r1, c1, soc, window_rmse
    """
    conn = get_connection()
    cur  = conn.cursor()

    cur.execute("""
        INSERT INTO ecm_params
            (test_id, recorded_at, window_index, r0, r1, c1, soc, window_rmse)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """, (
        test_id,
        params['recorded_at'],
        window_index,
        params['r0'],
        params['r1'],
        params['c1'],
        params['soc'],
        params['window_rmse'],
    ))

    conn.commit()
    cur.close()
    conn.close()
