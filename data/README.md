# Data Setup

This folder is not committed to the repo — data files are excluded via `.gitignore`.
Follow the instructions below to download and place the datasets before running the notebooks.

---

## Directory structure

```
data/
├── calce/
│   └── A1-007/
│       └── A1-007-OCV-10-20120629.xlsx
│   └── A1-008/
│       └── A1-008-OCV-10-20120629.xlsx
└── stanford/
    ├── Lithium-Ion Battery Cycle Life.csv
    ├── 100_Cycle_Lithium-Ion Battery Cycle Life.csv
    └── 50_Cycle_Lithium-Ion Battery Cycle Life.csv
```

---

## CALCE dataset

**Source:** https://calce.umd.edu/battery-data

**Cells used:**
- A1-007 — OCV test at -10°C (Arbin BT2000)
- A1-008 — OCV test at -10°C (Arbin BT2000)

**Download steps:**
1. Go to https://calce.umd.edu/battery-data
2. Find the **A123** section
3. Download the OCV test files for A1-007 and A1-008
4. Place each file in its own subfolder under `data/calce/`

**Notebook path setting:**
```python
DATA_DIR = r"path/to/data/calce"
```

---

## Stanford cycle life dataset

**Source:** https://data.matr.io/1/projects/5c48dd2bc625d700019f3204

**Paper:** Severson et al. 2019 — *Data-driven prediction of battery cycle life before capacity degradation*, Nature Energy

**Files needed:**
- `Lithium-Ion Battery Cycle Life.csv` — full dataset (140 cells, all cycles)
- `100_Cycle_Lithium-Ion Battery Cycle Life.csv` — first 100 cycles only
- `50_Cycle_Lithium-Ion Battery Cycle Life.csv` — first 50 cycles only

**Download steps:**
1. Go to the source URL above
2. Download all three CSV files
3. Place them in `data/stanford/`

**Notebook path setting:**
```python
DATA_DIR = r"path/to/data/stanford"
```

---

## battdb tables required

Before running any ingestion notebook, create these tables in battdb via pgAdmin:

```sql
CREATE TABLE ecm_predictions (
    id           SERIAL PRIMARY KEY,
    test_id      INTEGER NOT NULL,
    recorded_at  TEXT,
    window_index INTEGER NOT NULL,
    v_measured   NUMERIC NOT NULL,
    v_predicted  NUMERIC NOT NULL,
    abs_error    NUMERIC NOT NULL
);

CREATE TABLE ecm_params (
    id           SERIAL PRIMARY KEY,
    test_id      INTEGER NOT NULL,
    recorded_at  TEXT,
    window_index INTEGER NOT NULL,
    r0           NUMERIC NOT NULL,
    r1           NUMERIC NOT NULL,
    c1           NUMERIC NOT NULL,
    soc          NUMERIC NOT NULL,
    window_rmse  NUMERIC NOT NULL
);
```

---

## .env file

Create a `.env` file in the repo root (not committed) with your DB credentials:

```dotenv
DB_USERNAME=postgres
DB_PASSWORD=password
DB_TARGET=battdb
DB_HOSTNAME=localhost
DB_PORT=5454
```
