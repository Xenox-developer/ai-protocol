import json
import os
import sys
from getpass import getpass

from openai import OpenAI
from protocol_client import discover, execute, service_client


def main():
    with service_client() as client:
        operations = discover(client)
        if not operations:
            sys.exit("No operations are permitted for this token")
        task = input("What would you like to do: ")

        # Convert the service's operation descriptions into tools for the LLM.
        tools = []

        for item in operations:
            schema = item["input_schema"].copy()
            schema.pop("$schema", None)

            tools.append({
                "type": "function",
                "name": item["name"],
                "description": item["description"],
                "parameters": schema,
                "strict": False,
            })

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            api_key = getpass("OpenAI API key: ")

        with OpenAI(
            api_key=api_key,
            timeout=30,
            max_retries=0,
        ) as llm:
            decision = llm.responses.create(
                model="gpt-4.1-mini",
                instructions=(
                    "Complete the user's task using the available tools. "
                    "Choose at most one tool. "
                    "If you can fill in the required parameters from the user's "
                    "request, call the appropriate tool. "
                    "Do not ask for additional preferences "
                    "that are not required to call the operation. "
                    "Consider the operation description and parameter schema. "
                    "Ask for clarification only when a required parameter "
                    "cannot be determined without inventing it. "
                    "If no suitable tool is available, say so."
                ),
                input=task,
                tools=tools,
                tool_choice="auto",
                parallel_tool_calls=False,
            )

        calls = [
            item
            for item in decision.output
            if item.type == "function_call"
        ]

        # The model may respond with text instead of a tool call.
        if not calls:
            print(decision.output_text or "The model did not select an operation.")
            sys.exit(0)

        if len(calls) != 1:
            sys.exit("Expected a single operation call")

        call = calls[0]

        # Allow only an operation from the published list.
        operation = next(
            (item for item in operations if item["name"] == call.name),
            None,
        )

        if operation is None:
            sys.exit("The model selected an unknown operation")

        params = json.loads(call.arguments)

        print("The model selected:", operation["name"])
        print(
            "Parameters:",
            json.dumps(params, ensure_ascii=False),
        )

        result = execute(client, operation, params)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
