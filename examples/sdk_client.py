"""One adaptive client can safely serve concurrent calls in an agent process."""
import asyncio
import json
import os

from ai_protocol import AsyncClient, OperationError


async def main():
    async with AsyncClient(os.environ.get("SERVICE_URL", "http://127.0.0.1:3000"),
                           os.environ["AGENT_TOKEN_1"],
                           dispatcher_socket=os.environ.get("DISPATCH_SOCKET")) as client:
        operations = client.policy["operations"]
        print(json.dumps(operations, indent=2))
        name = input("Operation name: ")
        params = json.loads(input("Parameters as JSON: "))
        try:
            print(json.dumps(await client.call(name, params, timeout=120), indent=2))
        except OperationError as error:
            print(str(error))


if __name__ == "__main__":
    asyncio.run(main())
