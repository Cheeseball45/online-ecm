"""
ingest/nasa_csv.py

Ingests the NASA battery cycle dataset.
Columns: battery_id, cycle, voltage, temperature, capacity, soh, rul

Maps to battdb test_data_cycle_stats table via direct psycopg2 insertion.
"""

import json
import os
import pandas as pd
from tqdm import tqdm


def ingest(conn, dataset_cfg, base_dir="."):
    """
    Ingest the NASA battery CSV dataset.

    Parameters
    ----------
    conn       : psycopg2 connection to battdb
    dataset_cfg: dict from datasets.yml for this dataset
    base_dir   : root directory to resolve relative paths from
    """
    file_path = os.path.join(base_dir, dataset_cfg.get('file', ''))

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"NASA CSV not found: {file_path}")

    print(f"  Loading: {file_path}")
    df = pd.read_csv(file_path)
    print(f"  Rows: {len(df):,}  Batteries: {df['battery_id'].nunique()}")

    cell_meta_cfg = dataset_cfg.get('cell_meta', {})
    project       = dataset_cfg.get('project', 'NASA_Battery')
    customer      = dataset_cfg.get('customer', 'Public')

    success = 0
    failed  = 0

    for battery_id, group in tqdm(df.groupby('battery_id'), desc="  Ingesting cells"):
        try:
            _insert_cell(conn, battery_id, group, cell_meta_cfg, project, customer)
            success += 1
        except Exception as e:
            conn.rollback()
            print(f"\n  FAIL {battery_id}: {e}")
            failed += 1

    print(f"  Done — {success} cells ingested, {failed} failed")
    return success, failed


def _insert_cell(conn, battery_id, group, cell_meta_cfg, project, customer):
    cur = conn.cursor()
    pn = cell_meta_cfg.get('manufacturer_pn', 'NASA-18650')

    # 1. Get or create cells_meta
    cur.execute("SELECT cell_type_id FROM cells_meta WHERE manufacturer_pn = %s", (pn,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO cells_meta (manufacturer, manufacturer_pn, form_factor, capacity_mah, chemistry)
            VALUES (%s, %s, %s, %s, %s) RETURNING cell_type_id
        """, (
            cell_meta_cfg.get('manufacturer', 'NASA'),
            pn,
            cell_meta_cfg.get('form_factor', '18650'),
            cell_meta_cfg.get('capacity_mah', 2000),
            cell_meta_cfg.get('chemistry', 'LCO'),
        ))
        result = cur.fetchone()
    cell_type_id = result[0]

    # 2. Get or create cell
    cur.execute("SELECT cell_id FROM cells WHERE manufacturer_sn = %s", (battery_id,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO cells (manufacturer_sn, cell_type_id)
            VALUES (%s, %s) RETURNING cell_id
        """, (battery_id, cell_type_id))
        result = cur.fetchone()
    cell_id = result[0]

    # 3. Get or create test_meta
    test_name = f"NASA_{battery_id}"
    cur.execute("SELECT test_id FROM test_meta WHERE test_name = %s", (test_name,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO test_meta (test_name, cell_id, channel, comments)
            VALUES (%s, %s, %s, %s) RETURNING test_id
        """, (test_name, cell_id, 1, f"NASA battery dataset. Cell: {battery_id}"))
        result = cur.fetchone()
    test_id = result[0]

    # 4. Insert cycle stats
    rows = [
        (
            test_id,
            int(r['cycle']),
            round(float(r['capacity']) * 1000, 4),   # Ah -> mAh
            round(float(r['capacity']) * 1000, 4),
            None,
            json.dumps({
                'voltage_v':     round(float(r['voltage']), 4),
                'temperature_c': round(float(r['temperature']), 4),
                'soh':           round(float(r['soh']), 6),
                'rul':           int(r['rul']),
            })
        )
        for _, r in group.iterrows()
    ]

    cur.executemany("""
        INSERT INTO test_data_cycle_stats (
            test_id, cycle,
            reported_charge_capacity_mah,
            reported_discharge_capacity_mah,
            reported_charge_time_s,
            other_details
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """, rows)

    conn.commit()
    cur.close()
