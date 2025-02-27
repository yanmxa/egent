from dotenv import load_dotenv

import genpilot as gp

load_dotenv()

# model_options: https://platform.openai.com/docs/api-reference/chat/create
terminal = gp.TerminalChat()

model_config = model_options = {"temperature": 0.2, "stream": False}


def get_weather(location, time="now"):
    import json

    """Get the current weather in a given location. Location MUST be a city."""
    return json.dumps({"location": location, "temperature": "65", "time": time})


weather_observer = gp.Agent(
    name="Weather Observer",
    model_name="groq/llama-3.3-70b-versatile",
    model_config=model_config,
    chat=terminal,
    tools=[get_weather],
    description="I can get the current weather conditions for a specified city.",
    system="Your role focuses on retrieving and analyzing current weather conditions for a specified city. Your Responsibilities: Use the weather tool to find temperature. Typically, you only call the tool once and return the result. Do not call the weather with same input many times",
)

advisor = gp.Agent(
    name="Local Advisor",
    model_name="groq/llama-3.3-70b-versatile",
    model_config=model_config,
    chat=terminal,
    system="Specializes in understanding local fashion trends and cultural influences to recommend suitable clothing.",
)


traveller = gp.Agent(
    name="Traveller",
    model_name="groq/llama-3.3-70b-versatile",
    chat=terminal,
    handoffs=[weather_observer, advisor],
    system="This managerial role combines insights from both the Weather Observer and the Fashion and Culture Advisor to recommend appropriate clothing choices. Once you have the information for both Observer and Advisor. You can summarize give the final response. The final response with concise, straightforward items, like 1,2,3..",
    max_iter=10,
    memory=gp.memory.BufferMemory(30),
)

message = traveller("I want to go Xi 'an tomorrow. What should I wear?")
if message is None or isinstance(message, str):
    terminal.console.print_exception(message)
