# online-ecm

Online Equivalent Circuit Model (ECM) parameter estimation for lithium-ion batteries, orchestrated with Temporal and backed by BattDB (TimescaleDB).

## What it does

Reads battery test data from BattDB sample-by-sample, estimates ECM parameters (R₀, R₁, C₁) online using Recursive Least Squares (RLS), and persists predictions and parameter trajectories back to BattDB. Workflow execution is managed by Temporal for durability, retries, and observability.

## Architecture

```
BattDB (TimescaleDB)
    ↓  test_data (V, I, t)
Temporal Worker
    ├── load_window   — read N samples from BattDB
    ├── simulate      — run ECM forward with current params
    ├── reestimate    — RLS update → new θ = [R0, b]
    └── persist       — write predictions + params to BattDB
         ↓
    ecm_predictions   (V_measured, V_predicted per sample)
    ecm_params        (R0, R1, C1, SOC, RMSE per window)
         ↓
Grafana dashboard (localhost:3000)
```

## Repo structure

```
online-ecm/
├── ecm.py              # ECM simulation (V_t, V_rc, SOC update)
├── rls.py              # Recursive least squares estimator
├── db.py               # BattDB read/write helpers
├── activities.py       # Temporal activities (load · simulate · reestimate · persist)
├── workflow.py         # OnlineEcmRun workflow (deterministic, no numpy)
├── worker.py           # Temporal worker entry point
├── start_run.py        # CLI to kick off a workflow run
├── Dockerfile          # Worker container
├── requirements.txt
├── docker-compose-ecm.yml       # ECM worker service
├── docker-compose-temporal.yml  # Temporal + Grafana
├── grafana/
│   └── provisioning/
│       ├── datasources/battdb.yml
│       └── dashboards/
│           ├── provider.yml
│           └── ecm_dashboard.json
├── notebooks/
│   ├── phase1_offline_ecm.ipynb     # Static ECM baseline
│   └── phase2_rls_estimator.ipynb   # Online RLS validation
└── README.md
```

## Prerequisites

- Docker Desktop
- Python 3.11
- BattDB running on `localhost:5454` (postgres + pgbouncer containers on `battsoft-net`)

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/online-ecm.git
cd online-ecm
```

### 2. Install Python dependencies (for running locally / notebooks)

```bash
pip install -r requirements.txt
pip install jupyter pandas matplotlib scipy
```

### 3. Start Temporal + Grafana

```bash
cd temporal   # folder containing docker-compose-temporal.yml
mkdir -p temporal-dynamicconfig
# paste development-sql.yaml content into temporal-dynamicconfig/
docker compose -f docker-compose-temporal.yml up -d
```

Temporal UI: http://localhost:8080  
Grafana: http://localhost:3000 (admin / admin)

### 4. BattDB tables

Run this in pgAdmin against battdb:

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

## Running

### Locally (two terminals)

**Terminal 1 — worker:**
```bash
python worker.py
```

**Terminal 2 — start a run:**
```bash
python start_run.py --test CALCE_A1-007_OCV_neg10C_20120629 --lam 0.98
```

### In Docker

```bash
# Build and start the worker
docker compose -f docker-compose-ecm.yml up -d --build

# Start a run
docker compose -f docker-compose-ecm.yml run --rm ecm-starter \
    --test CALCE_A1-007_OCV_neg10C_20120629 --lam 0.98
```

## CLI options

| Flag | Default | Description |
|---|---|---|
| `--test` | required | `test_name` in BattDB |
| `--window` | 100 | Samples per window |
| `--reest` | 10 | Re-estimate every N windows |
| `--lam` | 0.98 | RLS forgetting factor (0 < λ ≤ 1) |
| `--alpha` | 0.97 | RC discretisation factor exp(-dt/τ) |

## ECM model

First-order Thevenin model:

```
V_t[k]    = V_oc(SOC[k]) - R0·I[k] - V_rc[k]
V_rc[k+1] = V_rc[k]·exp(-dt/τ) + R1·(1-exp(-dt/τ))·I[k]
SOC[k+1]  = SOC[k] - I[k]·dt / (Q_nom · 3600)
```

RLS regression form:

```
y[k]   = V_t[k] - V_oc[k] + α·(V_oc[k-1] - V_t[k-1])
phi[k] = [-I[k], I[k-1]]
theta  = [R0,  α·R0 - R1·(1-α)]
```

## Phase results (A1-007, LCO, -10°C OCV test)

| Phase | Method | RMSE |
|---|---|---|
| 1 | Static offline ECM (literature params) | 484.72 mV |
| 2 | Online RLS (λ=0.98) | 367.11 mV |
| Improvement | | +117.61 mV |
