"""Define a custom Reasoning and Action agent.

Works with a chat model with tool calling support.
"""

from datetime import UTC, datetime
from typing import Dict, List, Literal, cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from react_agent.configuration import Configuration
from react_agent.state import InputState, State
from react_agent.tools import TOOLS
from react_agent.utils import load_chat_model

# Define the function that calls the model


async def call_model(state: State) -> Dict[str, List[AIMessage]]:
    """Call the LLM powering our "agent".

    This function prepares the prompt, initializes the model, and processes the response.

    Args:
        state (State): The current state of the conversation.
        config (RunnableConfig): Configuration for the model run.

    Returns:
        dict: A dictionary containing the model's response message.
    """
    configuration = Configuration.from_context()

    # Initialize the model with tool binding. Change the model or add more tools here.
    model = load_chat_model(configuration.model,
                            base_url=configuration.base_url).bind_tools(TOOLS)

    # Format the system prompt. Customize this to change the agent's behavior.
    system_message = configuration.system_prompt.format(
        system_time=datetime.now(tz=UTC).isoformat()
    )

    # Get the model's response
    response = cast(
        AIMessage,
        await model.ainvoke(
            [{"role": "system", "content": system_message}, *state.messages]
        ),
    )

    # Handle the case when it's the last step and the model still wants to use a tool
    if state.is_last_step and response.tool_calls:
        return {
            "messages": [
                AIMessage(
                    id=response.id,
                    content="Sorry, I could not find an answer to your question in the specified number of steps.",
                )
            ]
        }

    # Return the model's response as a list to be added to existing messages
    return {"messages": [response]}

def human_review_node(state: State) -> Command[Literal["call_model", "tools"]]:
    """Handle human review of the model's output.

    This function is a placeholder for human review logic. It currently returns the
    model's output without any modifications.

    Args:
        state (State): The current state of the conversation.

    Returns:
        Command: A command to continue the conversation with the model's output.
    """
    # In a real implementation, this would involve human review logic
    last_message = state.messages[-1]
    tool_call = last_message.tool_calls[0]

    # 这是用于提供给用户确信的信息，通过Command来实现(resume=<human_review>)
    human_review = interrupt(
        {
            "question": "工具调用正确吗？",
            "tool_call": tool_call
        }
    )

    review_action = human_review["action"]
    review_data = human_review.get("data")

    # 如果用户同意执行
    if review_action == "continue":
        # 这里可以添加更多的逻辑来处理用户的反馈
        return Command(goto="tools")
    elif review_action == "update":
        updated_message = AIMessage(
            id=last_message.id,
            content=last_message.content,
            tool_calls=[{
                "id": tool_call["id"],
                "name": tool_call["name"],
                # 这里使用人工更新的参数执行
                "args": review_data,
            }],
        )
        return Command(
            goto="tools",
            update={"messages": [updated_message]})
    elif review_action == "feedback":
        tool_message = ToolMessage(content = review_data,
                                   tool_call_id = tool_call["id"],
                                   name = tool_call["name"])
        return Command(goto="call_model",
                       update={"messages": [tool_message]})
    else:
        updated_message = HumanMessage(content=f"拒绝调用工具{tool_call['name']}")
        return Command(goto="call_model",
                       update={"messages": [updated_message]}) 

# Define a new graph

def subgraph_node(state:State):
    return Command(
        goto="__end__",
        update={
            "messages": [
                AIMessage(
                    content="子图执行完毕，返回主图继续执行"
                )
            ]
        }
    )

builder = StateGraph(State, input=InputState, config_schema=Configuration)
subgraph_builder = StateGraph(State)
subgraph_builder.add_node("subgraph_node", subgraph_node)
subgraph_builder.add_edge("__start__", "subgraph_node")
subgraph = subgraph_builder.compile(name="subgraph")

# Define the two nodes we will cycle between
builder.add_node(call_model)
builder.add_node("human_review", human_review_node)
builder.add_node("tools", ToolNode(TOOLS))
builder.add_node("subgraph", subgraph)

# Set the entrypoint as `call_model`
# This means that this node is the first one called
builder.add_edge("__start__", "call_model")


def route_model_output(state: State) -> Literal["subgraph", "human_review"]:
    """Determine the next node based on the model's output.

    This function checks if the model's last message contains tool calls.

    Args:
        state (State): The current state of the conversation.

    Returns:
        str: The name of the next node to call ("subgraph" or "human_review").
    """
    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(
            f"Expected AIMessage in output edges, but got {type(last_message).__name__}"
        )
    # If there is no tool call, then we finish
    if not last_message.tool_calls:
        return "subgraph"
    # Otherwise we execute the requested actions
    return "human_review"


# Add a conditional edge to determine the next step after `call_model`
builder.add_conditional_edges(
    "call_model",
    # After call_model finishes running, the next node(s) are scheduled
    # based on the output from route_model_output
    route_model_output,
)

# Add a normal edge from `tools` to `call_model`
# This creates a cycle: after using tools, we always return to the model
builder.add_edge("tools", "call_model")
builder.add_edge("subgraph", "__end__")

# Compile the builder into an executable graph
graph = builder.compile(name="ReAct Agent")
