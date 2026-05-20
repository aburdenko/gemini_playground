# Name
Travel Concierge Test

# Instruction
You are a travel concierge agent. Your goal is to help users book flights.

# Tools
[
    {
        "type": "FunctionTool",
        "name": "search_flights"
    },
    {
        "type": "FunctionTool",
        "name": "book_flight"
    }
]

# Prompt
I want to book a flight from SFO to LAX on 2024-12-01.
