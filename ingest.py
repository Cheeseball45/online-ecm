"""
ingest.py — Dataset ingestion CLI for online-ecm.

Reads datasets.yml and ingests all enabled datasets into battdb.

Usage:
    python ingest.py                          # ingest all enabled datasets
    python ingest.py --dataset Stanford_CycleLife_2019   # ingest one dataset by name
    python ingest.py --list                   # list all datasets in the registry
    python ingest.py --dry-run                # show what would be ingested without doing it

Add new datasets by editing datasets.yml — no code changes needed.
"""

import argparse
import os
import sys
import psycopg2
import yaml

# Import handlers
from ingest import stanford_csv, nasa_csv, arbin

HANDLERS = {
    'stanford_csv': stanford_csv.ingest,
    'nasa_csv':     nasa_csv.ingest,
    'arbin':        arbin.ingest,
}

# DB connection — reads from environment variables with local defaults
DB_CONFIG = {
    'host':     os.environ.get('DB_HOST',     'localhost'),
    'port':     int(os.environ.get('DB_PORT', '5454')),
    'dbname':   os.environ.get('DB_NAME',     'battdb'),
    'user':     os.environ.get('DB_USER',     'postgres'),
    'password': os.environ.get('DB_PASSWORD', 'password'),
}

REGISTRY_FILE = 'datasets.yml'
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))


def load_registry():
    registry_path = os.path.join(BASE_DIR, REGISTRY_FILE)
    if not os.path.exists(registry_path):
        print(f"Registry not found: {registry_path}")
        sys.exit(1)
    with open(registry_path) as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description='Ingest battery datasets into battdb')
    parser.add_argument('--dataset',  help='Ingest a specific dataset by name')
    parser.add_argument('--list',     action='store_true', help='List all registered datasets')
    parser.add_argument('--dry-run',  action='store_true', help='Show what would be ingested')
    parser.add_argument('--all',      action='store_true', help='Ingest all enabled datasets')
    args = parser.parse_args()

    registry = load_registry()
    datasets = registry.get('datasets', [])

    # ── List ──────────────────────────────────────────────────────────────
    if args.list:
        print(f"\nRegistered datasets ({len(datasets)} total):\n")
        for d in datasets:
            status = 'enabled' if d.get('enabled', True) else 'disabled'
            print(f"  [{status:8s}] {d['name']}  ({d['source']})")
        print()
        return

    # ── Filter ────────────────────────────────────────────────────────────
    if args.dataset:
        targets = [d for d in datasets if d['name'] == args.dataset]
        if not targets:
            print(f"Dataset not found: {args.dataset}")
            print("Run 'python ingest.py --list' to see available datasets")
            sys.exit(1)
    else:
        targets = [d for d in datasets if d.get('enabled', True)]

    if not targets:
        print("No enabled datasets found. Check datasets.yml")
        return

    # ── Dry run ───────────────────────────────────────────────────────────
    if args.dry_run:
        print(f"\nDry run — would ingest {len(targets)} dataset(s):\n")
        for d in targets:
            print(f"  {d['name']}  ({d['source']})")
        print()
        return

    # ── Connect ───────────────────────────────────────────────────────────
    print(f"\nConnecting to battdb at {DB_CONFIG['host']}:{DB_CONFIG['port']}...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        print("Connected.\n")
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    # ── Ingest ────────────────────────────────────────────────────────────
    total_success = 0
    total_failed  = 0

    for dataset in targets:
        name   = dataset['name']
        source = dataset['source']

        print(f"{'='*60}")
        print(f"Dataset: {name}  ({source})")
        print(f"{'='*60}")

        if source not in HANDLERS:
            print(f"  No handler for source type: {source}")
            print(f"  Supported: {list(HANDLERS.keys())}")
            continue

        try:
            handler = HANDLERS[source]
            success, failed = handler(conn, dataset, base_dir=BASE_DIR)
            total_success += success
            total_failed  += failed
        except Exception as e:
            print(f"  ERROR ingesting {name}: {e}")
            conn.rollback()
            total_failed += 1

        print()

    conn.close()

    print(f"{'='*60}")
    print(f"Ingestion complete")
    print(f"  Total success: {total_success}")
    print(f"  Total failed:  {total_failed}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
