import asyncio

import dotenv
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from lark_agent import root_agent


async def main():
    dotenv.load_dotenv()  # Load environment variables from .env file if present
    session_service = InMemorySessionService()
    await session_service.create_session(app_name='app', user_id='user',
                                         session_id='92b9a134-df38-4e47-b814-4554f1c91657')
    runner = Runner(agent=root_agent, app_name='app', session_service=session_service)

    print("--- Lark Agent CLI Started ---")
    print("Type 'exit' or 'quit' to end the session.\n")

    while True:
        try:
            user_input = input("User: ")
            if user_input.lower() in ['exit', 'quit']:
                break

            if not user_input.strip():
                continue

            content = types.Content(role='user', parts=[types.Part(text=user_input)])

            async for event in runner.run_async(user_id='user', session_id='92b9a134-df38-4e47-b814-4554f1c91657',
                                                new_message=content):
                if event.is_final_response() and event.content and event.content.parts:
                    print("Agent: " + event.content.parts[0].text, end="", flush=True)
            print("\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == '__main__':
    asyncio.run(main())
