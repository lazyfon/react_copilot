from langgraph_sdk import get_client
import asyncio

client = get_client(url="http://127.0.0.1:2024/")

async def main():
    async for chunk in client.runs.stream(
        None,  # Threadless run
        "agent", # Name of assistant. Defined in langgraph.json.
        input={
        "messages": [{
            "role": "human",
            "content": "Where is the capital of China?",
            }],
        },
        config={
            "configurable": {
                "system_prompt": "You are a helpful assistant. Answer the question as best you can.",
                "model": "openai/Qwen/Qwen3-32B",
                "base_url": "https://api.siliconflow.cn/v1",
            },
        }
    ):
        print(f"Receiving new event of type: {chunk.event}...")
        import json
        print(json.dumps(chunk.data, indent=4))
        print("\n\n")

asyncio.run(main())