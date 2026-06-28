import asyncio
import os
from temporalio.client import Client
from temporalio.worker import Worker

from workflow import OnlineEcmRun
from activities import load_window, simulate, reestimate, persist

TEMPORAL_ADDRESS = os.environ.get('TEMPORAL_ADDRESS', 'localhost:7233')
TASK_QUEUE       = 'ecm-queue'

async def main():
    client = await Client.connect(TEMPORAL_ADDRESS)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[OnlineEcmRun],
        activities=[load_window, simulate, reestimate, persist],
    )
    print(f'ECM worker started on task queue: {TASK_QUEUE}')
    print(f'Temporal server: {TEMPORAL_ADDRESS}')
    print('Waiting for workflows... (Ctrl+C to stop)')
    await worker.run()

if __name__ == '__main__':
    asyncio.run(main())