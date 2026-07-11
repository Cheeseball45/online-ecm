"""
ingest/stanford_csv.py

Ingests the Severson et al. 2019 Stanford cycle life CSV dataset.
Columns: battery_id, cycle, QC, QD, IR, Tavg, Tmin, Tmax, chargetime,
         cycle_life, C1, Q1, C2

Maps to battdb test_data_cycle_stats table via direct psycopg2 insertion.
"""

import json
import os
import pandas as pd
from tqdm import tqdm


def ingest(conn, dataset_cfg, base_dir="."):
    """
    Ingest the Stanford CSV dataset.

    Parameters
    ----------
    conn       : psycopg2 connection to battdb
    dataset_cfg: dict from datasets.yml for this dataset
    base_dir   : root directory to resolve relative paths from
    """
    files = dataset_cfg.get('files', {})
    full_path = os.path.join(base_dir, files.get('full', ''))

    if not os.path.exists(full_path):
        raise FileNotFoundError(f"Stanford CSV not found: {full_path}")

    print(f"  Loading: {full_path}")
    df = pd.read_csv(full_path)
    print(f"  Rows: {len(df):,}  Batteries: {df['battery_id'].nunique()}")

    cell_meta_cfg = dataset_cfg.get('cell_meta', {})
    project       = dataset_cfg.get('project', 'Stanford_CycleLife_2019')
    customer      = dataset_cfg.get('customer', 'Public')

    # Per-cell summary for protocol info
    cell_summary = df.groupby('battery_id').agg(
        cycle_life=('cycle_life', 'first'),
        C1=('C1', 'first'),
        Q1=('Q1', 'first'),
        C2=('C2', 'first'),
        batch=('battery_id', lambda x: x.iloc[0][:2])
    ).reset_index()

    success = 0
    failed  = 0

    for battery_id, group in tqdm(df.groupby('battery_id'), desc="  Ingesting cells"):
        try:
            _insert_cell(
                conn, battery_id, group,
                cell_summary, cell_meta_cfg,
                project, customer
            )
            success += 1
        except Exception as e:
            conn.rollback()
            print(f"\n  FAIL {battery_id}: {e}")
            failed += 1

    print(f"  Done — {success} cells ingested, {failed} failed")
    return success, failed


def _insert_cell(conn, battery_id, group, cell_summary, cell_meta_cfg, project, customer):
    cur = conn.cursor()
    row   = cell_summary[cell_summary['battery_id'] == battery_id].iloc[0]
    batch = battery_id.split('c')[0]
    c1 = row['C1'] if pd.notna(row['C1']) else 0
    q1 = row['Q1'] if pd.notna(row['Q1']) else 0
    c2 = row['C2'] if pd.notna(row['C2']) else 0

    pn = cell_meta_cfg.get('manufacturer_pn', 'APR18650M1A')

    # 1. Get or create cells_meta
    cur.execute("SELECT cell_type_id FROM cells_meta WHERE manufacturer_pn = %s", (pn,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO cells_meta (manufacturer, manufacturer_pn, form_factor, capacity_mah, chemistry)
            VALUES (%s, %s, %s, %s, %s) RETURNING cell_type_id
        """, (
            cell_meta_cfg.get('manufacturer', 'A123 Systems'),
            pn,
            cell_meta_cfg.get('form_factor', '18650'),
            cell_meta_cfg.get('capacity_mah', 1100),
            cell_meta_cfg.get('chemistry', 'LFP/graphite'),
        ))
        result = cur.fetchone()
    cell_type_id = result[0]

    # 2. Get or create cell
    cur.execute("SELECT cell_id FROM cells WHERE manufacturer_sn = %s", (battery_id,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO cells (manufacturer_sn, cell_type_id, batch_number)
            VALUES (%s, %s, %s) RETURNING cell_id
        """, (battery_id, cell_type_id, batch))
        result = cur.fetchone()
    cell_id = result[0]

    # 3. Get or create test_meta
    test_name = f"Stanford_CycleLife_{battery_id}"
    cur.execute("SELECT test_id FROM test_meta WHERE test_name = %s", (test_name,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO test_meta (test_name, cell_id, channel, comments)
            VALUES (%s, %s, %s, %s) RETURNING test_id
        """, (test_name, cell_id, 1,
              f"Severson et al. 2019. Protocol: {c1}C to {q1}% SOC, then {c2}C."))
        result = cur.fetchone()
    test_id = result[0]

    # 4. Insert cycle stats
    rows = [
        (
            test_id,
            int(r['cycle']),
            round(r['QC'] * 1000, 4),
            round(r['QD'] * 1000, 4),
            round(r['chargetime'] * 60, 1),
            json.dumps({
                'IR_ohm': round(float(r['IR']), 6),
                'Tavg_c': round(float(r['Tavg']), 4),
                'Tmin_c': round(float(r['Tmin']), 4),
                'Tmax_c': round(float(r['Tmax']), 4),
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
