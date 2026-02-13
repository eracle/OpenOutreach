# linkedin/actions/template.py
import logging
from pathlib import Path

import jinja2
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from linkedin.conf import AI_MODEL, LLM_API_KEY, LLM_API_BASE

logger = logging.getLogger(__name__)


def call_llm(prompt: str) -> str:
    """Call an LLM to generate content based on the prompt using LangChain and OpenAI."""
    # Check for required values; fail if missing
    if LLM_API_KEY is None:
        raise ValueError("LLM_API_KEY is not set in the environment or config.")

    logger.debug("Calling '%s'", AI_MODEL)

    # Initialize the LangChain ChatOpenAI model with explicit API key
    llm = ChatOpenAI(model=AI_MODEL, temperature=0.7, api_key=LLM_API_KEY, base_url=LLM_API_BASE)

    # Create a simple prompt template
    chat_prompt = ChatPromptTemplate.from_messages([
        ("human", "{prompt}"),
    ])

    # Chain the prompt with the LLM
    chain = chat_prompt | llm

    # Invoke the chain with the prompt
    response = chain.invoke({"prompt": prompt})

    # Extract the generated content
    return response.content.strip()


def render_template(session: "AccountSession", template_file: str, template_type: str, profile: dict) -> str:
    context = {**profile}

    logger.debug("Available template variables: %s", sorted(context.keys()))

    template_path = Path(template_file)
    folder = template_path.parent  # folder of the template itself
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(folder))
    template = env.get_template(template_path.name)  # load just the filename

    rendered = template.render(**context).strip()
    logger.debug(f"Rendered template: {rendered}")

    match template_type:
        case 'jinja':
            pass
        case 'ai_prompt':
            rendered = call_llm(rendered)
        case _:
            raise ValueError(f"Unknown template_type: {template_type}")

    booking_link = session.account_cfg.get("booking_link", None)
    rendered += f"\n{booking_link}" if booking_link else ""
    return rendered
