# run_workflow.py
import asyncio
from temporalio.client import Client

from workflows import LinkedInWorkflow

async def main():
    # Connect to the local Temporal server
    client = await Client.connect("localhost:7233")

    # Example params with a list of profile URLs
    params = {
        'login_creds': {'username': 'test', 'password': 'test'},
        'profile_urls': [f'https://www.linkedin.com/in/john-doe{i}' for i in range(100)],
        'message': 'Hello, nice to connect!'
    }

    # Start and await the workflow result
    result = await client.execute_workflow(
        LinkedInWorkflow.run,
        params,  # Input to the workflow
        id="linkedin-workflow",  # Unique workflow ID
        task_queue="linkedin-task-queue"  # Must match the worker's queue
    )
    print(f"Result: {result}")

if __name__ == "__main__":
    asyncio.run(main())