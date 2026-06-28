"""
start_run.py — Kick off an OnlineEcmRun workflow for a given test.

Usage:
    python start_run.py --test CALCE_A1-007_OCV_neg10C_20120629
    python start_run.py --test CALCE_A1-007_OCV_neg10C_20120629 --window 200 --lam 0.98
"""

import asyncio
import argparse
from temporalio.client import Client

import db
from workflow import OnlineEcmRun

TEMPORAL_ADDRESS = 'localhost:7233'
TASK_QUEUE       = 'ecm-queue'


async def main():
    parser = argparse.ArgumentParser(description='Start an OnlineEcmRun workflow')
    parser.add_argument('--test',    required=True,  help='test_name in BattDB')
    parser.add_argument('--window',  type=int,   default=100,  help='Window size (samples)')
    parser.add_argument('--reest',   type=int,   default=10,   help='Re-estimate every N windows')
    parser.add_argument('--lam',     type=float, default=0.98, help='RLS forgetting factor')
    parser.add_argument('--alpha',   type=float, default=0.97, help='RC discretisation factor')
    args = parser.parse_args()

    # Look up test_id from BattDB
    test_id    = db.get_test_id(args.test)
    total_rows = db.get_test_row_count(test_id)

    print(f'Test:       {args.test}')
    print(f'test_id:    {test_id}')
    print(f'Total rows: {total_rows:,}')
    print(f'Window:     {args.window}  Re-estimate every: {args.reest}  λ={args.lam}')
    print()

    # Initial state — theta=[R0_guess, 0], P=identity*1000
    init_state = {
        'theta':  [0.1, 0.0],
        'P':      [1000.0, 0.0, 0.0, 1000.0],
        'v_rc':   0.0,
        'soc':    1.0,
        'alpha':  args.alpha,
        'dt_nom': 3.0,
    }

    cfg = {
        'test_id':          test_id,
        'test_name':        args.test,
        'window_size':      args.window,
        'reestimate_every': args.reest,
        'lam':              args.lam,
        'total_rows':       total_rows,
        'start_offset':     0,
        'window_index':     0,
        'init_state':       init_state,
    }

    client = await Client.connect(TEMPORAL_ADDRESS)

    workflow_id = f'ecm-run-{args.test}-w{args.window}-l{args.lam}'

    handle = await client.start_workflow(
        OnlineEcmRun.run,
        cfg,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    print(f'Workflow started!')
    print(f'  Workflow ID: {workflow_id}')
    print(f'  Run ID:      {handle.result_run_id}')
    print(f'  UI:          http://localhost:8080/namespaces/default/workflows/{workflow_id}')
    print()
    print('Waiting for result...')

    result = await handle.result()
    print()
    print('Run complete!')
    print(f'  Windows run:     {result["windows_run"]}')
    print(f'  Rows processed:  {result["rows_processed"]:,}')
    print(f'  Final SOC:       {result["final_state"]["soc"]:.3f}')
    print(f'  Final theta:     {result["final_state"]["theta"]}')


if __name__ == '__main__':
    asyncio.run(main())
