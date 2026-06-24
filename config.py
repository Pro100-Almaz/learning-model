import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ANTHRO_API_KEY = os.getenv("ANTHROPIC_API_KEY")

STORYTELLER_MODEL = "gpt-5-mini"

CRITIC_MODEL = "o3"
# Redrafts the Storyteller gets after its first draft before the graph breaks
# out to the fallback (arch.md §5.4). Total attempts = 1 + MAX_REVISIONS; at 2
# the breakout triggers after a draft "fails more than twice".
MAX_REVISIONS = 2

TUTOR_MODEL = "claude-sonnet-4-6"

config = Config()