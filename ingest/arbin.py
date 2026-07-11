"""
ingest/arbin.py

Ingests Arbin BT2000 Excel exports (.xlsx) directly via psycopg2.
Each xlsx file has an Info sheet and a Channel_* data sheet.

Maps to battdb test_data table (full timeseries).
"""

import json
import os
import pandas as pd
from tqdm import tqdm


def get_data_sheet(path):
    xl = pd.ExcelFile(path)
    sheets = [s for s in xl.sheet_names if s not in ('Info', 'Sheet1')]
    if not sheets:
        raise ValueError(f"No data sheet found in {path}")
    return sheets[0]


def ingest(conn, dataset_cfg, base_dir="."):
    """
    Ingest an Arbin dataset (one or more cells, each with one or more xlsx files).

    Parameters
    ----------
    conn       : psycopg2 connection to battdb
    dataset_cfg: dict from datasets.yml for this dataset
    base_dir   : root directory to resolve relative paths from
    """
    cell_meta_cfg = dataset_cfg.get('cell_meta', {})
    cycler_meta_cfg = dataset_cfg.get('cycler_meta', {})
    project  = dataset_cfg.get('project', 'CALCE')
    customer = dataset_cfg.get('customer', 'Public')
    cells    = dataset_cfg.get('cells', [])

    success = 0
    failed  = 0

    for cell_cfg in tqdm(cells, desc="  Ingesting cells"):
        cell_folder = os.path.join(base_dir, cell_cfg.get('cell_folder', ''))
        if not os.path.exists(cell_folder):
            print(f"\n  SKIP {cell_cfg.get('manufacturer_sn')} — folder not found: {cell_folder}")
            continue

        xlsx_files = sorted([
            f for f in os.listdir(cell_folder) if f.endswith('.xlsx')
        ])
        if not xlsx_files:
            print(f"\n  SKIP {cell_cfg.get('manufacturer_sn')} — no xlsx files found")
            continue

        try:
            _insert_cell(
                conn, cell_cfg, xlsx_files, cell_folder,
                cell_meta_cfg, cycler_meta_cfg,
                project, customer, dataset_cfg
            )
            success += 1
        except Exception as e:
            conn.rollback()
            print(f"\n  FAIL {cell_cfg.get('manufacturer_sn')}: {e}")
            failed += 1

    print(f"  Done — {success} cells ingested, {failed} failed")
    return success, failed


def _insert_cell(conn, cell_cfg, xlsx_files, cell_folder,
                 cell_meta_cfg, cycler_meta_cfg,
                 project, customer, dataset_cfg):
    cur = conn.cursor()

    battery_id = cell_cfg['manufacturer_sn']
    pn = cell_meta_cfg.get('manufacturer_pn', 'CALCE-A1')

    # Read and concatenate all xlsx files for this cell
    dfs = []
    for fname in xlsx_files:
        path = os.path.join(cell_folder, fname)
        sheet = get_data_sheet(path)
        df_part = pd.read_excel(path, sheet_name=sheet)
        dfs.append(df_part)
    df = pd.concat(dfs, ignore_index=True).sort_values('Date_Time').reset_index(drop=True)

    # 1. Get or create cells_meta
    cur.execute("SELECT cell_type_id FROM cells_meta WHERE manufacturer_pn = %s", (pn,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO cells_meta (manufacturer, manufacturer_pn, form_factor, chemistry)
            VALUES (%s, %s, %s, %s) RETURNING cell_type_id
        """, (
            cell_meta_cfg.get('manufacturer', 'Unknown'),
            pn,
            cell_meta_cfg.get('form_factor', 'prismatic'),
            cell_meta_cfg.get('chemistry', 'LCO'),
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
        """, (battery_id, cell_type_id, cell_cfg.get('batch_number', '')))
        result = cur.fetchone()
    cell_id = result[0]

    # 3. Get or create cyclers_meta
    cycler_model = cycler_meta_cfg.get('model', 'BT2000')
    cur.execute("SELECT cycler_type_id FROM cyclers_meta WHERE model = %s", (cycler_model,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO cyclers_meta (manufacturer, model)
            VALUES (%s, %s) RETURNING cycler_type_id
        """, (cycler_meta_cfg.get('manufacturer', 'Arbin'), cycler_model))
        result = cur.fetchone()
    cycler_type_id = result[0]

    # 4. Get or create cycler
    cycler_sn = cell_cfg.get('cycler_sn', f'ARBIN-{battery_id}')
    cur.execute("SELECT cycler_id FROM cyclers WHERE sn = %s", (cycler_sn,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO cyclers (sn, cycler_type_id, location)
            VALUES (%s, %s, %s) RETURNING cycler_id
        """, (cycler_sn, cycler_type_id, 'Unknown'))
        result = cur.fetchone()
    cycler_id = result[0]

    # 5. Get or create schedule_meta
    schedule_name = cell_cfg.get('schedule_name', f'{battery_id}_schedule')
    cur.execute("SELECT schedule_id FROM schedule_meta WHERE schedule_name = %s", (schedule_name,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO schedule_meta (schedule_name, cycler_make, test_type)
            VALUES (%s, %s, %s) RETURNING schedule_id
        """, (schedule_name, 'Arbin', cell_cfg.get('test_type', 'Characterization')))
        result = cur.fetchone()
    schedule_id = result[0]

    # 6. Get or create test_meta
    test_name = f"{dataset_cfg.get('name', 'CALCE')}_{battery_id}"
    cur.execute("SELECT test_id FROM test_meta WHERE test_name = %s", (test_name,))
    result = cur.fetchone()
    if not result:
        cur.execute("""
            INSERT INTO test_meta (
                test_name, cell_id, channel, comments,
                schedule_id, cycler_id,
                first_recorded_datetime, last_recorded_datetime
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING test_id
        """, (
            test_name, cell_id, 1,
            cell_cfg.get('comments', ''),
            schedule_id, cycler_id,
            df['Date_Time'].min(), df['Date_Time'].max()
        ))
        result = cur.fetchone()
    test_id = result[0]

    # 7. Insert test_data rows
    rows = [
        (
            test_id,
            int(r['Cycle_Index']),
            int(r['Step_Index']),
            round(r['Test_Time(s)'], 3),
            round(r['Step_Time(s)'], 3),
            round(r['Current(A)'] * 1000, 4),
            round(r['Voltage(V)'] * 1000, 4),
            r['Date_Time'].isoformat() if pd.notna(r['Date_Time']) else None,
            round(r['Test_Time(s)'], 0),
            [round(float(r['Temperature (C)_1']), 4),
             round(float(r['Temperature (C)_2']), 4)],
            json.dumps({
                'dVdt':                   round(float(r['dV/dt(V/s)']), 8),
                'IR_ohm':                 round(float(r['Internal_Resistance(Ohm)']), 6),
                'AC_impedance':           round(float(r['AC_Impedance(Ohm)']), 6),
                'ACI_phase_deg':          round(float(r['ACI_Phase_Angle(Deg)']), 4),
                'charge_capacity_mah':    round(r['Charge_Capacity(Ah)'] * 1000, 6),
                'discharge_capacity_mah': round(r['Discharge_Capacity(Ah)'] * 1000, 6),
                'charge_energy_mwh':      round(r['Charge_Energy(Wh)'] * 1000, 6),
                'discharge_energy_mwh':   round(r['Discharge_Energy(Wh)'] * 1000, 6),
            })
        )
        for _, r in df.iterrows()
    ]

    cur.executemany("""
        INSERT INTO test_data (
            test_id, cycle, step,
            test_time_s, step_time_s,
            current_ma, voltage_mv,
            recorded_datetime, unixtime_s,
            thermocouple_temps_c, other_details
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """, rows)

    conn.commit()
    cur.close()
