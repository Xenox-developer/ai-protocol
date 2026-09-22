import json
import sys

from protocol_client import discover, execute, service_client


def main():
    with service_client() as client:
        operations = discover(client)
        if not operations:
            sys.exit("No operations are permitted for this token")
        print("Available operations:")
        for number, operation in enumerate(operations, start=1):
            print(f"{number}. {operation['name']} — {operation['description']}")
        number = int(input("Operation number: "))
        if not 1 <= number <= len(operations):
            sys.exit("No such operation")
        operation = operations[number - 1]
        print("Parameter schema:")
        print(json.dumps(operation["input_schema"], ensure_ascii=False, indent=2))
        params = json.loads(input("Parameters as JSON: "))
        result = execute(client, operation, params)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
