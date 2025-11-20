# linkedin/conf.py
import os

from dotenv import load_dotenv

load_dotenv()

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)
AI_MODEL = os.getenv("AI_MODEL", 'gpt-4o-mini')
