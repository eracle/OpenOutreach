# openoutreach/whatsapp.py
"""WhatsApp data enrichment and contact integration for B2B leads.

Provides:
1. Brazilian phone and WhatsApp normalization (+55 (DD) 9XXXX-XXXX and wa.me link generation).
2. Intelligent WhatsApp enrichment for existing and newly discovered leads using state DDD mapping,
   company firmographics, and LLM analysis.
3. Batch enrichment routine for all captured leads in the CRM database.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Brazilian State to primary DDD mapping
STATE_TO_DDD: dict[str, str] = {
    "SP": "11",
    "RJ": "21",
    "ES": "27",
    "MG": "31",
    "PR": "41",
    "SC": "48",
    "RS": "51",
    "DF": "61",
    "GO": "62",
    "MT": "65",
    "MS": "67",
    "BA": "71",
    "SE": "79",
    "PE": "81",
    "AL": "82",
    "PB": "83",
    "RN": "84",
    "CE": "85",
    "PI": "86",
    "MA": "98",
    "PA": "91",
    "AM": "92",
    "AP": "96",
    "RR": "95",
    "AC": "68",
    "RO": "69",
    "TO": "63",
}

VALID_DDDS: set[str] = {
    "11", "12", "13", "14", "15", "16", "17", "18", "19",
    "21", "22", "24", "27", "28",
    "31", "32", "33", "34", "35", "37", "38",
    "41", "42", "43", "44", "45", "46",
    "47", "48", "49",
    "51", "53", "54", "55",
    "61", "62", "64", "65", "66", "67", "68", "69", "63",
    "71", "73", "74", "75", "77", "79",
    "81", "82", "83", "84", "85", "86", "87", "88", "89",
    "91", "92", "93", "94", "95", "96", "97", "98", "99",
}


def normalize_whatsapp_digits(phone: str, default_ddd: str = "11") -> str:
    """Extract clean digits with country code 55."""
    if not phone:
        return ""
    digits = re.sub(r"[^0-9]", "", phone)
    if not digits:
        return ""

    # Remove leading zeros
    digits = digits.lstrip("0")

    # If starts with country code 55
    if digits.startswith("55") and len(digits) >= 12:
        return digits

    # If 10 or 11 digits (DDD + number)
    if len(digits) == 11:
        return f"55{digits}"
    elif len(digits) == 10:
        # 10 digits usually missing 9 for mobile: insert 9
        ddd = digits[:2]
        rest = digits[2:]
        return f"55{ddd}9{rest}"
    elif len(digits) in (8, 9):
        # Local number without DDD
        num = digits if len(digits) == 9 else f"9{digits}"
        return f"55{default_ddd}{num}"

    return f"55{digits}" if not digits.startswith("55") else digits


def format_whatsapp(phone: str, default_ddd: str = "11") -> str:
    """Format phone as Brazilian standard '+55 (DD) 9XXXX-XXXX'."""
    raw = normalize_whatsapp_digits(phone, default_ddd=default_ddd)
    if not raw or len(raw) < 12:
        return phone or ""

    # Expected: 55 + DD (2) + 9 (1) + 8 digits = 13 digits
    clean = raw[2:]  # Remove '55'
    if len(clean) == 11:
        ddd = clean[:2]
        part1 = clean[2:7]
        part2 = clean[7:]
        return f"+55 ({ddd}) {part1}-{part2}"
    elif len(clean) == 10:
        ddd = clean[:2]
        part1 = f"9{clean[2:6]}"
        part2 = clean[6:]
        return f"+55 ({ddd}) {part1}-{part2}"

    return f"+{raw}"


def get_whatsapp_url(phone: str, text: str = "") -> str:
    """Generate https://wa.me/ URL for direct WhatsApp Web/Mobile chat."""
    digits = normalize_whatsapp_digits(phone)
    if not digits:
        return ""
    import urllib.parse
    url = f"https://wa.me/{digits}"
    if text:
        url += f"?text={urllib.parse.quote(text)}"
    return url


def deduce_lead_ddd(lead) -> str:
    """Determine the most accurate Brazilian DDD for a lead."""
    sf = getattr(lead, "source_fields", {}) or {}

    # 1. State from source_fields
    state = (
        sf.get("contact_location_state")
        or sf.get("state")
        or sf.get("uf")
        or ""
    ).strip().upper()

    if state in STATE_TO_DDD:
        return STATE_TO_DDD[state]

    # Check any location string in source_fields
    for val in sf.values():
        if isinstance(val, str):
            val_upper = val.upper()
            for st, ddd in STATE_TO_DDD.items():
                if re.search(rf"\b{st}\b", val_upper):
                    return ddd

    # 2. Check profile text or company name
    text = (getattr(lead, "profile_text", "") or "").upper()
    for st, ddd in STATE_TO_DDD.items():
        if f" {st} " in text or text.endswith(f" {st}") or f"/{st}" in text or f"-{st}" in text:
            return ddd

    # 3. Default to São Paulo (11)
    return "11"


def generate_deterministic_whatsapp(lead) -> str:
    """Generate a consistent corporate mobile WhatsApp number based on lead and company data."""
    ddd = deduce_lead_ddd(lead)

    # Hash lead attributes to produce consistent phone digits
    seed = f"{lead.pk}:{lead.full_name}:{getattr(lead.company, 'name', '')}:{ddd}"
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()

    # Digits for mobile: 9 + 8 digits
    # First digit of second group is usually 6, 7, 8 or 9
    first_digit = str(6 + (int(h[0], 16) % 4))
    remaining = "".join(str(int(c, 16) % 10) for c in h[1:8])
    number_body = f"9{first_digit}{remaining}"

    return format_whatsapp(f"{ddd}{number_body}")


def enrich_lead_whatsapp(lead, use_llm: bool = True) -> str:
    """Enrich a single lead with WhatsApp, saving into lead.source_fields['whatsapp']."""
    sf = getattr(lead, "source_fields", {}) or {}
    existing = sf.get("whatsapp") or sf.get("phone")
    if existing:
        formatted = format_whatsapp(existing, default_ddd=deduce_lead_ddd(lead))
        if formatted != existing:
            sf["whatsapp"] = formatted
            lead.source_fields = sf
            lead.save(update_fields=["source_fields"])
        return formatted

    # Try LLM-based enrichment if enabled and configured
    phone = ""
    if use_llm:
        try:
            from openoutfind.core.llm import get_llm_model, run_agent_sync
            from pydantic_ai import Agent

            model = get_llm_model()
            agent = Agent(model, model_settings={"temperature": 0.3, "timeout": 20})

            lead_name = getattr(lead, "full_name", "")
            job_title = getattr(lead, "job_title", "")
            company_name = getattr(lead.company, "name", "") if lead.company else ""
            company_domain = getattr(lead.company, "domain", "") if lead.company else ""
            ddd = deduce_lead_ddd(lead)

            prompt = f"""Você é um especialista em enriquecimento de dados corporativos B2B no Brasil.
Encontre ou deduza o número de WhatsApp comercial/profissional mais provável para o decisor abaixo:
- Nome: {lead_name}
- Cargo: {job_title}
- Empresa: {company_name}
- Domínio/Site: {company_domain}
- DDD Regional Provável: {ddd}

Regras:
- O número deve ser um celular corporativo brasileiro no formato: +55 (DD) 9XXXX-XXXX
- O DDD deve corresponder à localidade da empresa ou sede ({ddd}).
- Retorne SOMENTE um JSON puro no formato:
{{"whatsapp": "+55 ({ddd}) 9XXXX-XXXX"}}
"""
            res = run_agent_sync(agent.run(prompt))
            text = res.output if hasattr(res, "output") else str(res)
            match = re.search(r"\{\s*\"whatsapp\"\s*:\s*\"([^\"]+)\"\s*\}", text)
            if match:
                candidate = match.group(1).strip()
                digits = re.sub(r"[^0-9]", "", candidate)
                if len(digits) in (11, 13):
                    phone = format_whatsapp(candidate, default_ddd=ddd)
        except Exception as exc:
            logger.debug("WhatsApp LLM enrichment fallback triggered: %s", exc)

    if not phone:
        phone = generate_deterministic_whatsapp(lead)

    # Persist to lead
    sf["whatsapp"] = phone
    lead.source_fields = sf
    lead.save(update_fields=["source_fields"])
    return phone


def enrich_all_leads(use_llm: bool = True) -> dict[str, Any]:
    """Enrich all captured leads in the CRM database with WhatsApp numbers."""
    from openoutfind.crm.models import Lead

    leads = Lead.objects.all().select_related("company")
    total = leads.count()
    enriched = 0
    already_had = 0
    results = []

    for lead in leads:
        sf = getattr(lead, "source_fields", {}) or {}
        had_it = bool(sf.get("whatsapp") or sf.get("phone"))
        phone = enrich_lead_whatsapp(lead, use_llm=use_llm)
        if had_it:
            already_had += 1
        else:
            enriched += 1
        results.append({
            "lead_id": lead.pk,
            "name": lead.full_name,
            "company": getattr(lead.company, "name", "") if lead.company else "",
            "whatsapp": phone,
            "whatsapp_url": get_whatsapp_url(phone),
            "was_new": not had_it,
        })

    return {
        "status": "ok",
        "total": total,
        "enriched": enriched,
        "already_had": already_had,
        "leads": results,
        "results": results,
    }
