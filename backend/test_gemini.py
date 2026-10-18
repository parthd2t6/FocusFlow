
import os
from pathlib import Path

from google import genai

# Read the API key from the project's root .env file.
env_path = Path(__file__).resolve().parent.parent / ".env"

if not env_path.exists():
    raise FileNotFoundError("Project .env file was not found.")

api_key = None

for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()

    if line.startswith("GEMINI_API_KEY="):
        api_key = line.split("=", 1)[1].strip().strip("\"'")
        break

if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing from the .env file.")

print("API key found. Sending a small test request...")

try:
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents="Reply with exactly: FocusFlow Gemini connection works."
    )

    print("\nGemini response:")
    print(response.text or "Gemini returned an empty response.")

except Exception as exc:
    # Never print the API key.
    print(f"\nGemini request failed: {type(exc).__name__}")
    print("Check your API key, model access, internet connection,")
    print("and Google AI Studio usage limits or billing requirements.")
    print("Your existing application code has not been changed.")