# workflows.py
import asyncio
import random
from datetime import timedelta

from temporalio import activity
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker



@activity.defn
async def linkedin_login(creds: dict) -> str:
    print(f"Logging in with credentials: {creds}")
    return "Login successful"


@activity.defn
async def send_connection_request(profile_url: str) -> str:
    print(f"Sending connection request to profile: {profile_url}")
    return "Request sent"


@activity.defn
async def check_connection_accepted(profile_url: str) -> bool:
    print(f"Checking if connection accepted for profile: {profile_url}")
    # Simulate non-acceptance with 0.9 probability
    if random.random() < 0.9:
        print(f"Connection not yet accepted for {profile_url}")
        return False
    else:
        print(f"Connection accepted for {profile_url}")
        return True


@activity.defn
async def send_message(params: dict) -> str:
    print(f"Sending message to profile {params['profile_url']}: {params['message']}")
    return "Message sent"


@workflow.defn
class LinkedInWorkflow:
    @workflow.run
    async def run(self, params: dict) -> str:
        # params expected to contain: 'login_creds', 'profile_urls' (list), 'message'
        # Execute login once
        login_result = await workflow.execute_activity(
            linkedin_login,
            params['login_creds'],
            start_to_close_timeout=timedelta(seconds=10)
        )

        # Define an async function to handle each profile
        async def handle_profile(profile_url: str) -> str:
            # Send connection request
            await workflow.execute_activity(
                send_connection_request,
                profile_url,
                start_to_close_timeout=timedelta(seconds=10)
            )

            # Poll for connection acceptance with max attempts to prevent infinite loop
            accepted = False
            attempts = 0
            max_attempts = 20  # Adjust as needed to limit runtime
            while not accepted and attempts < max_attempts:
                accepted = await workflow.execute_activity(
                    check_connection_accepted,
                    profile_url,
                    start_to_close_timeout=timedelta(seconds=10)
                )
                if not accepted:
                    await workflow.sleep(
                        timedelta(seconds=5))  # Short delay for simulation/testing; increase for real use
                attempts += 1

            if accepted:
                # Send message once accepted
                send_result = await workflow.execute_activity(
                    send_message,
                    {'profile_url': profile_url, 'message': params['message']},
                    start_to_close_timeout=timedelta(seconds=10)
                )
                return send_result
            else:
                return f"Failed to get acceptance for {profile_url} after {max_attempts} attempts"

        # Run all profile handlers in parallel
        tasks = [handle_profile(url) for url in params['profile_urls']]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results (handle any exceptions if needed)
        success_count = sum(1 for r in results if isinstance(r, str) and "Message sent" in r)

        return f"LinkedIn workflow completed for {len(params['profile_urls'])} profiles ({success_count} successful)"


async def main():
    # Connect to the local Temporal server
    client = await Client.connect("localhost:7233", namespace="default")

    # Create and run the worker
    worker = Worker(
        client,
        task_queue="linkedin-task-queue",  # Task queue name (can be anything unique)
        workflows=[LinkedInWorkflow],
        activities=[linkedin_login, send_connection_request, check_connection_accepted, send_message]
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
