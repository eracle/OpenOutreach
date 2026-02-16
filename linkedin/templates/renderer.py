# linkedin/templates/renderer.py
import logging
from pathlib import Path

import jinja2
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from linkedin.conf import AI_MODEL, LLM_API_KEY, LLM_API_BASE, PRODUCT_DOCS_FILE

logger = logging.getLogger(__name__)


def call_llm(prompt: str) -> str:
    """Call an LLM to generate content based on the prompt using LangChain and OpenAI."""
    if LLM_API_KEY is None:
        raise ValueError("LLM_API_KEY is not set in the environment or config.")

    logger.debug("Calling '%s'", AI_MODEL)

    llm = ChatOpenAI(model=AI_MODEL, temperature=0.7, api_key=LLM_API_KEY, base_url=LLM_API_BASE)

    chat_prompt = ChatPromptTemplate.from_messages([
        ("human", "{prompt}"),
    ])

    chain = chat_prompt | llm
    response = chain.invoke({"prompt": prompt})

    return response.content.strip()


def render_template(session: "AccountSession", template_file: str, profile: dict) -> str:
    context = {**profile}

    product_description = ""
    if PRODUCT_DOCS_FILE.exists():
        product_description = PRODUCT_DOCS_FILE.read_text(encoding="utf-8").strip()
    context["product_description"] = product_description

    logger.debug("Available template variables: %s", sorted(context.keys()))

    template_path = Path(template_file)
    folder = template_path.parent
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(folder))
    template = env.get_template(template_path.name)

    rendered = template.render(**context).strip()
    logger.debug(f"Rendered template: {rendered}")

    rendered = call_llm(rendered)

    booking_link = session.account_cfg.get("booking_link", None)
    rendered += f"\n{booking_link}" if booking_link else ""
    return rendered
