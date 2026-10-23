"""Compatibility CLI for the installed local dispatcher connector."""
from ai_protocol import DispatcherClient

if __name__ == "__main__":
    import argparse
    import asyncio
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("operation")
    parser.add_argument("params")
    args = parser.parse_args()
    result = asyncio.run(DispatcherClient().call(args.operation, json.loads(args.params)))
    print(json.dumps(result))
    raise SystemExit(0 if result["ok"] else 1)
