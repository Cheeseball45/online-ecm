import asyncio
from temporalio.client import Client
from hello_worker import HelloWorkflow

async def main():
    client = await Client.connect("localhost:7233")
    result = await client.execute_workflow(
        HelloWorkflow.run,
        "BattStudio",
        id="hello-workflow-1",
        task_queue="hello-queue",
    )
    print(f"Result: {result}")

if __name__ == "__main__":
    asyncio.run(main())