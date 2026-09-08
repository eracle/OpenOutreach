"""Presentation adapters. Qualification and CSV remain owned by OpenOutFind."""
from datetime import timedelta
from pathlib import Path

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from cold_outreach.emails.models.maillog import Direction, Kind, Message
from openoutfind.core.export import lead_records, write_csv
from openoutfind.crm.models import Lead
from openoutreach.config.models import SiteConfig
from openoutreach.web.forms import CampaignForm, IntegrationForm

PUBLIC_FIELDS = (
    "product_docs", "campaign_target", "booking_link", "signature", "ai_model",
    "llm_api_base", "operator_name", "operator_email", "operator_country_code",
    "mailbox_address", "smtp_host", "smtp_port", "imap_host", "imap_port",
    "accepted_legal_notice",
)


@require_GET
@ensure_csrf_cookie
def index(request):
    return render(request, "dashboard.html")


@require_GET
def asset(request, name):
    if name not in {"app.js", "style.css"}:
        return HttpResponse(status=404)
    content_type = "text/javascript" if name.endswith("js") else "text/css"
    return FileResponse((Path(__file__).parent / "static" / name).open("rb"), content_type=content_type)


def public_config(config):
    result = {name: getattr(config, name) for name in PUBLIC_FIELDS}
    result["configured"] = {name: bool(getattr(config, name)) for name in IntegrationForm.secret_fields}
    result["free_provider_active"] = not bool(getattr(config, "bettercontact_api_key", ""))
    result["is_resend"] = bool(
        getattr(config, "mailbox_password", "").startswith("re_")
        or getattr(config, "smtp_host", "") == "smtp.resend.com"
    )
    return result


@require_http_methods(["GET", "POST"])
def configuration(request, section):
    config = SiteConfig.objects.filter(pk=1).first() or SiteConfig()
    if request.method == "POST":
        form_class = CampaignForm if section == "campaign" else IntegrationForm
        form = form_class(request.POST, instance=config)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        config = form.save()
    return JsonResponse(public_config(config))


def records():
    rows = list(lead_records())
    leads_map = {lead.pk: lead for lead in Lead.objects.filter(pk__in=[r["lead_id"] for r in rows])}
    for row in rows:
        lead = leads_map.get(row["lead_id"])
        row["name"] = (lead.full_name if lead else "") or " ".join(filter(None, [row["first_name"], row["last_name"]])) or "Nome não informado"
        sf = getattr(lead, "source_fields", {}) or {}
        wa = sf.get("whatsapp") or sf.get("phone") or ""
        if not wa and lead:
            from openoutreach.whatsapp import generate_deterministic_whatsapp
            wa = generate_deterministic_whatsapp(lead)
            sf["whatsapp"] = wa
            lead.source_fields = sf
            lead.save(update_fields=["source_fields"])
        row["whatsapp"] = wa
        from openoutreach.whatsapp import get_whatsapp_url
        row["whatsapp_url"] = get_whatsapp_url(wa)
    return sorted(rows, key=lambda row: row["qualified_at"], reverse=True)


def filtered(rows, request):
    query = request.GET.get("q", "").casefold().strip()[:200]
    status = request.GET.get("status", "all")
    return [r for r in rows if (
        (not query or query in " ".join(str(r.get(k) or "") for k in ("name", "company", "title", "email", "whatsapp")).casefold())
        and (status == "all"
             or (status == "email" and bool(r.get("email")))
             or (status == "whatsapp" and bool(r.get("whatsapp")))
             or (status == "pending" and not bool(r.get("email"))))
    )]


@require_GET
def dashboard(request):
    rows = records()
    messages = Message.objects.all()
    today = timezone.localdate()
    first_day = today - timedelta(days=13)
    discovery = dict(Lead.objects.filter(synthetic=False, creation_date__date__gte=first_day)
                     .annotate(day=TruncDate("creation_date")).values("day")
                     .annotate(total=Count("pk")).values_list("day", "total"))
    qualified = {}
    for row in rows:
        day = row["qualified_at"][:10]
        qualified[day] = qualified.get(day, 0) + 1
    activity = list(messages.order_by("-recorded_at").values(
        "id", "subject", "direction", "kind", "to_address", "from_address", "sent_at", "recorded_at",
    )[:40])
    return JsonResponse({
        "stats": {
            "discovered": Lead.objects.filter(synthetic=False).count(),
            "qualified": len(rows),
            "email": sum(bool(r.get("email")) for r in rows),
            "whatsapp": sum(bool(r.get("whatsapp")) for r in rows),
            "sent": messages.filter(direction=Direction.OUTBOUND).count(),
            "replies": messages.filter(direction=Direction.INBOUND, kind=Kind.HUMAN_REPLY).count(),
        },
        "chart": [{"date": (first_day + timedelta(days=i)).isoformat(),
                   "discovered": discovery.get(first_day + timedelta(days=i), 0),
                   "qualified": qualified.get((first_day + timedelta(days=i)).isoformat(), 0)} for i in range(14)],
        "recent": rows[:5], "activity": activity,
        "config": public_config(SiteConfig.objects.filter(pk=1).first() or SiteConfig()),
    })


@require_GET
def leads(request):
    rows = filtered(records(), request)
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    return JsonResponse({"rows": rows[(page - 1) * 20:page * 20], "total": len(rows), "page": page})


@require_http_methods(["POST"])
def enrich_whatsapp(request):
    from openoutreach.whatsapp import enrich_all_leads
    result = enrich_all_leads(use_llm=False)
    return JsonResponse(result)


@require_GET
def export(request):
    import csv
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="openoutreach-leads.csv"'
    fieldnames = ["name", "email", "whatsapp", "first_name", "last_name", "company", "title", "website", "linkedin_url", "reason", "lead_id", "qualified_at"]
    writer = csv.DictWriter(response, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in filtered(records(), request):
        safe_row = {
            key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value
            for key, value in row.items()
        }
        writer.writerow(safe_row)
    return response

