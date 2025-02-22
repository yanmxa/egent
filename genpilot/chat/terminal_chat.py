import rich.console
import sys
import rich
import rich.rule
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.text import Text
from rich.panel import Panel
from rich.markdown import Markdown
from rich.padding import Padding
import threading
from rich.padding import Padding
from typing import Callable, Any, List, Tuple, Union
import time
from datetime import datetime
import json
from enum import Enum

from litellm import completion
from litellm.utils import (
    ModelResponse,
    CustomStreamWrapper,
    ChatCompletionDeltaToolCall,
)
from openai.types.chat import (
    ChatCompletionUserMessageParam,
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageToolCall,
)
from openai.types.chat.chat_completion_message_tool_call import Function

from ..abc.agent import IAgent
from ..abc.chat import IChat


class ActionPermission(Enum):
    AUTO = "auto"
    ALWAYS = "always"
    NONE = "none"


class TerminalChat(IChat):
    # Optional OpenAI params: see https://platform.openai.com/docs/api-reference/chat/create
    def __init__(self, model_options):
        self.console = rich.get_console()
        self.model_options = model_options
        self.avatars = {
            "user": "👦",
            "assistant": "🤖",
            "system": "💻",
            "tool": "🛠",
        }

    def input(
        self, message: ChatCompletionMessageParam | str | None, agent: IAgent
    ) -> ChatCompletionMessageParam | None:
        if isinstance(message, str):
            message = ChatCompletionUserMessageParam(
                content=message, role="user", name="user"
            )
            agent.memory.add(message)
        elif message is None:
            is_stop = self._ask_input(
                agent, exit=["exit", "quit"]
            )  # add the message to memory
            if is_stop:
                return None
            message = agent.memory.last()  # TODO: it may not right to get the last one
        else:
            agent.memory.add(message)

        content = message.get("content")
        role = message.get("role")
        name = message.get("name")

        self.forward_print(
            from_role=role, from_agent=name, to_agent=agent.name, content=content
        )
        return message

    def forward_print(self, from_role, from_agent, to_agent, content):
        avatar = self.avatars.get(from_agent, self.avatars.get(from_role))
        timestamp = datetime.now().strftime("%H:%M:%S")
        title = f"{avatar} [bold bright_cyan]{from_agent}[/bold bright_cyan] ➫ {to_agent}: [dim green]({timestamp})[/]"

        print()
        self.console.rule(title, align="left", style="dim")
        markdown = Markdown(content)
        self.console.print(Padding(markdown, (1, 0, 0, 3)), end="")

    def reasoning(self, agent: IAgent) -> ChatCompletionAssistantMessageParam:
        """
        Facilitates interaction with the LLM model via the aisuite client.

        Args:
            agent (IAgent): The agent to reason with the LLM model.
            client (Client): The aisuite client to interact with the model.
        """
        tools = list(agent.tools.values())

        response = None
        try:
            response = completion(
                model=agent.model,
                messages=agent.memory.get(),
                tools=tools,
                **self.model_options,
            )
        except Exception as e:
            self.console.print(agent.memory.get())
            print(f"Exception Message: {str(e)}")
            import traceback

            traceback.print_exc()

        completion_message = self.reasoning_print(response, agent)

        message = completion_message.model_dump(mode="json")
        # for 'role:assistant' the following must be satisfied[('messages.2' : property 'refusal' is unsupported
        if message["role"] == "assistant" and "refusal" in message:
            del message["refusal"]
        return message

    def reasoning_print(
        self, response: Union[ModelResponse, CustomStreamWrapper], agent: IAgent
    ) -> ChatCompletionMessage:
        # avatar = self.avatars.get(
        #     agent.name, self.avatars.get(agent.memory.last()["role"], "")
        # )
        # timestamp = datetime.now().strftime("%H:%M:%S")
        # title = (
        #     f"{avatar} [bold cyan]{agent.name}[/bold cyan]: [dim green]({timestamp})[/]"
        # )
        # self.console.rule(title, align="left", style="dim")
        # print()

        completion_message = ChatCompletionMessage(role="assistant")
        if isinstance(response, CustomStreamWrapper):
            completion_message_tool_calls: List[ChatCompletionMessageToolCall] = []
            completion_message_content = ""
            for chunk in response:
                delta = chunk.choices[0].delta
                # print(delta, end="\n")
                # not print tool in here
                if delta.tool_calls is not None:
                    for delta_tool_call in delta.tool_calls:
                        # tool_call is ChatCompletionDeltaToolCall
                        completion_message_tool_calls.append(
                            ChatCompletionMessageToolCall(
                                id=delta_tool_call.id,
                                function=Function(
                                    arguments=delta_tool_call.function.arguments,
                                    name=delta_tool_call.function.name,
                                ),
                                type=delta_tool_call.type,
                            )
                        )
                if delta.content is not None and delta.content != "":
                    # delta print
                    # self.console.print(delta.content, end="")
                    completion_message_content += delta.content
            if len(completion_message_tool_calls) > 0:
                completion_message.tool_calls = completion_message_tool_calls
            if completion_message_content != "":
                completion_message.content = completion_message_content
                # self.console.print()
        else:
            completion_message = response.choices[0].message
        return completion_message

    def stream_with_indent(self, text: str, indent: int = 4):
        # Splits the text into lines and indents each line
        indented_text = "\n".join((" " * indent) + line for line in text.splitlines())
        return indented_text

        # Streaming output (you could use sys.stdout or directly print)
        # sys.stdout.write(indented_text + "\n")

    def acting(self, agent: IAgent) -> List[ChatCompletionToolMessageParam]:
        chat_message_param = agent.memory.last()
        if chat_message_param["tool_calls"] is None:
            return None

        tool_messages = []
        for tool_call in chat_message_param["tool_calls"]:
            func_name = tool_call["function"]["name"]
            func_args = tool_call["function"]["arguments"]
            tool_call_id = tool_call["id"]
            if isinstance(func_args, str):
                func_args = json.loads(func_args)

            # validate
            if not func_name in agent.functions:
                raise ValueError(f"The '{func_name}' isn't registered!")

            # check the permission
            if not self.before_action(
                agent,
                func_name,
                func_args,
                func_edit=0,
                functions=agent.functions,
            ):
                tool_observation = ChatCompletionToolMessageParam(
                    tool_call_id=tool_call_id,
                    content=f"Action({func_name}: {tool_call_id}) are not allowed by the user.",
                    role="tool",
                    name=func_name,
                )
                tool_messages.append(tool_observation)
                continue

            func = agent.functions[func_name]
            return_type = func.__annotations__.get("return")

            if return_type is not None and issubclass(return_type, IAgent):
                # invoke agent
                target_agent: IAgent = func(**func_args)
                # self.console.print(f" 🔃 {agent.name} forward to {target_agent.name}")
                task: str = func_args["message"]
                # TODO: do we need to convert the message into tool message param?
                target_agent_obs = target_agent.run(
                    ChatCompletionUserMessageParam(
                        role="user", content=task, name=agent.name
                    )
                )
                if target_agent_obs is None:
                    raise ValueError("The agent observation is None!")
                if isinstance(target_agent_obs, str):
                    raise ValueError(f"{target_agent.name} error: {target_agent_obs}")

                content = target_agent_obs["content"]
                self.forward_print(
                    from_role=target_agent_obs["role"],
                    from_agent=target_agent.name,
                    to_agent=agent.name,
                    content=content,
                )
                # covert the assistant response into tool call response
                tool_messages.append(
                    ChatCompletionToolMessageParam(
                        tool_call_id=tool_call_id,
                        content=f"{content}",
                        role="tool",
                        name=func_name,
                    )
                )

            else:
                # invoke function
                tool_result = self.invoke(func, func_args, agent)
                tool_obs = ChatCompletionToolMessageParam(
                    tool_call_id=tool_call_id,
                    # tool_name=tool_call.function.name, # tool name is not supported by groq client now
                    content=f"{tool_result}",
                    role="tool",
                    name=func_name,
                )
                tool_messages.append(tool_obs)

        if len(tool_messages) == 0:
            self.console.print(
                f"🐞 observe nothing for message: {chat_message_param} \n", style="red"
            )
        return tool_messages

    def invoke(self, func, args, agent: IAgent) -> str:

        # markdown = Markdown(content)
        # self.console.print(Padding(markdown, (1, 3)), end="")

        result = func(**args)

        text = Text(f"{result}")
        text.stylize("dim")
        self.console.print(Padding(text, (0, 0, 1, 3)))  # Top, Right, Bottom, Left
        # chat_console.print(text, padding=(0, 0, 0, 2))
        # chat_console.print(f"{message}", style="italic dim")
        # obs_str = content
        # if self.validate_obs:
        #     obs_str = self.validate_observation(content)
        # return obs_str
        return result

    def before_action(
        self, agent: IAgent, func_name, func_args, func_edit=0, functions={}
    ) -> bool:
        # check the agent function
        permission = agent.permission
        func = functions[func_name]
        return_type = func.__annotations__.get("return")
        if return_type is not None and issubclass(return_type, IAgent):
            # TODO: add other information
            return True

        avatar = self.avatars.get(
            agent.name, self.avatars.get(agent.memory.last()["role"], "")
        )
        timestamp = datetime.now().strftime("%H:%M:%S")
        title = (
            f"{avatar} [bold cyan]{agent.name}[/bold cyan]: [dim green]({timestamp})[/]"
        )
        print()
        self.console.rule(title, align="left", style="dim")
        print()

        tool_info = f"  🛠  [yellow]{func_name}[/yellow] - [dim]{func_args}[/dim]"
        if func_name == "code_executor":
            self.console.print(f"  🛠  [yellow]{func_args['language']}[/yellow]")
            rich.print()
            self.console.print(
                Syntax(
                    func_args["code"],
                    func_args["language"],
                    theme="monokai",
                    line_numbers=True,
                )
            )
        elif func_name == "kubectl_cmd":
            block = func_args["command"] + func_args["input"]
            self.console.print(
                f"  🛠  [yellow]cluster: {func_args['cluster_name']}[/yellow]"
            )
            rich.print()
            self.console.print(
                Syntax(
                    block,
                    "shell",
                    theme="monokai",
                    line_numbers=True,
                )
            )
        else:
            self.console.print(tool_info)
        rich.print()

        if permission == ActionPermission.NONE:
            return True

        if permission == ActionPermission.AUTO and func_edit == 0:  # enable auto
            return True

        # chat_console.print(f"🛠  [yellow]{func_name}[/yellow]\n")
        # chat_console.print(f"   [dim]{func_args} [/dim] \n")
        while True:
            proceed = self.console.input(f"  👉 [dim]Approve ?: [/dim]").strip().upper()
            rich.print()
            if proceed == "Y":
                return True
            elif proceed == "N":
                self.console.print(f"🚫 Action is canceled by user \n", style="red")
                return False
            else:
                self.console.print(
                    "⚠️ Invalid input! Please enter 'Y' or 'N'.\n", style="yellow"
                )

    def _ask_input(
        self,
        agent: IAgent,
        exit=["exit", "quit"],
        finish=["done"],
        prompt_ask=" 🧘 ",
    ) -> bool:
        """_summary_

        Args:
            agent (IAgent): _description_
            exit (list, optional): _description_. Defaults to [].
            finish (list, optional): _description_. Defaults to [].
            prompt_ask (str, optional): _description_. Defaults to " 🧘 ".

        Returns:
            bool: is stop
        """
        while True:
            user_input = input(prompt_ask).strip().lower()
            # user_input = Prompt.ask(prompt_ask).strip().lower()
            print()

            if user_input in exit:
                return True

            if user_input in finish:
                return False

            match user_input:

                case "":
                    continue

                case "exit" | "e":
                    self.console.print("👋 [blue]Goodbye![/blue]")
                    print()
                    return None

                case "/debug":
                    self.console.print(agent.memory.get())
                    continue

                case "/debugtool":
                    for key, val in agent.functions.items():
                        self.console.print(val)
                    continue

                case "/pop":
                    msg = agent.memory.last()
                    self.console.print(msg)
                    continue

                case "/add" | "/a":
                    input_content = (
                        user_input.replace("/add", "").replace("/a", "").strip()
                    )
                    if input_content:
                        agent.memory.add(
                            ChatCompletionUserMessageParam(
                                content=input_content,
                                role="user",
                                name="user",
                            )
                        )
                    continue

                case "/clear":
                    agent.memory.clear()
                    continue

                case _:
                    agent.memory.add(
                        ChatCompletionUserMessageParam(
                            content=input_content,
                            role="user",
                            name="user",
                        )
                    )
                    return False


def spinner(stop_event: threading.Event) -> None:
    """
    Displays a simple spinner in the terminal until the stop_event is set.

    Parameters:
    stop_event (threading.Event): Event to stop the spinner when the task finishes.
    """
    spinner_chars = ["|", "/", "-", "\\"]
    while not stop_event.is_set():  # Continue spinning until the event is set
        for char in spinner_chars:
            if stop_event.is_set():
                break  # Exit immediately if the stop event is set
            sys.stdout.write(
                f"\r{char} "
            )  # \r moves the cursor to the beginning of the line
            sys.stdout.flush()  # Ensures the spinner character is immediately displayed
            time.sleep(0.1)  # Adjust the speed of the spinner

    # To clear the spinner when done, overwrite the spinner with spaces and move the cursor to the start
    sys.stdout.write("\r    \r")  # Overwrites the spinner with spaces (clear the line)
    sys.stdout.flush()
