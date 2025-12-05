# linkedin/actions/template.py
import logging
from typing import Dict, Any

import jinja2
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from linkedin.conf import AI_MODEL, OPENAI_API_KEY

logger = logging.getLogger(__name__)


def call_llm(prompt: str) -> str:
    """Call an LLM to generate content based on the prompt using LangChain and OpenAI."""
    # Check for required values; fail if missing
    if OPENAI_API_KEY is None:
        raise ValueError("OPENAI_API_KEY is not set in the environment or config.")

    logger.info(f"Calling LLM with model '{AI_MODEL}' and prompt: {prompt[:50]}...")

    # Initialize the LangChain ChatOpenAI model with explicit API key
    llm = ChatOpenAI(model=AI_MODEL, temperature=0.7, api_key=OPENAI_API_KEY)  # Pass API key explicitly

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


def render_template(template_file: str, template_type: str, context: Dict[str, Any]) -> str:
    """Renders the template based on the type, optionally passing to an LLM."""
    with open(template_file, 'r') as f:
        template_str = f.read()

    if template_type == 'jinja':
        template = jinja2.Template(template_str)
        return template.render(**context).strip()

    elif template_type == 'ai_prompt':
        template = jinja2.Template(template_str)
        prompt = template.render(**context).strip()
        return call_llm(prompt)

    else:
        raise ValueError(f"Unknown template_type: {template_type}")
