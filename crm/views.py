# crm/views.py
"""Custom CRM views — professional UI replacing Django Admin."""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST

from chat.models import ChatMessage
from crm.models import Deal, Lead
from linkedin.enums import ProfileState
from linkedin.models import ActionLog, Campaign, LinkedInProfile, SearchKeyword, SiteConfig, Task


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    now = timezone.now()
    today = now.date()
    week_ago = now - timedelta(days=7)

    total_leads = Lead.objects.count()
    active_leads = Lead.objects.filter(disqualified=False).count()
    total_deals = Deal.objects.count()
    total_campaigns = Campaign.objects.count()

    # Deal state distribution
    state_counts = {}
    for state in ProfileState:
        state_counts[state.value] = Deal.objects.filter(state=state.value).count()

    # Recent activity
    actions_today = ActionLog.objects.filter(created_at__date=today).count()
    actions_week = ActionLog.objects.filter(created_at__gte=week_ago).count()

    # Tasks
    pending_tasks = Task.objects.filter(status="pending").count()
    running_tasks = Task.objects.filter(status="running").count()
    failed_tasks = Task.objects.filter(status="failed").count()

    # Recent leads
    recent_leads = Lead.objects.order_by("-creation_date")[:10]

    # Recent deals
    recent_deals = Deal.objects.select_related("lead", "campaign").order_by("-update_date")[:10]

    # Pipeline funnel data
    pipeline = {
        "qualified": Deal.objects.filter(state=ProfileState.QUALIFIED).count(),
        "ready": Deal.objects.filter(state=ProfileState.READY_TO_CONNECT).count(),
        "pending": Deal.objects.filter(state=ProfileState.PENDING).count(),
        "connected": Deal.objects.filter(state=ProfileState.CONNECTED).count(),
        "completed": Deal.objects.filter(state=ProfileState.COMPLETED).count(),
        "failed": Deal.objects.filter(state=ProfileState.FAILED).count(),
    }

    context = {
        "total_leads": total_leads,
        "active_leads": active_leads,
        "total_deals": total_deals,
        "total_campaigns": total_campaigns,
        "state_counts": state_counts,
        "actions_today": actions_today,
        "actions_week": actions_week,
        "pending_tasks": pending_tasks,
        "running_tasks": running_tasks,
        "failed_tasks": failed_tasks,
        "recent_leads": recent_leads,
        "recent_deals": recent_deals,
        "pipeline": pipeline,
    }
    return render(request, "crm/dashboard.html", context)


@login_required
def dashboard_chart_data(request):
    """JSON endpoint for dashboard charts."""
    now = timezone.now()
    data = {"labels": [], "actions": [], "leads": []}
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).date()
        data["labels"].append(day.strftime("%b %d"))
        data["actions"].append(ActionLog.objects.filter(created_at__date=day).count())
        data["leads"].append(Lead.objects.filter(creation_date__date=day).count())
    return JsonResponse(data)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

class LeadListView(LoginRequiredMixin, View):
    def get(self, request):
        q = request.GET.get("q", "").strip()
        status = request.GET.get("status", "")
        campaign_id = request.GET.get("campaign", "")

        leads = Lead.objects.all().order_by("-creation_date")
        if q:
            leads = leads.filter(
                Q(first_name__icontains=q) | Q(last_name__icontains=q)
                | Q(company_name__icontains=q) | Q(public_identifier__icontains=q)
            )
        if status == "disqualified":
            leads = leads.filter(disqualified=True)
        elif status == "active":
            leads = leads.filter(disqualified=False)

        if campaign_id:
            leads = leads.filter(deals__campaign_id=campaign_id).distinct()

        campaigns = Campaign.objects.all()
        ctx = {"leads": leads[:200], "q": q, "status": status, "campaigns": campaigns, "campaign_id": campaign_id}
        if request.headers.get("HX-Request"):
            return render(request, "crm/partials/lead_table.html", ctx)
        return render(request, "crm/leads.html", ctx)


class LeadDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        deals = Deal.objects.filter(lead=lead).select_related("campaign")
        messages = ChatMessage.objects.filter(
            content_type__model="lead", object_id=lead.pk
        ).order_by("creation_date")
        profile = lead.profile_data or {}
        return render(request, "crm/lead_detail.html", {
            "lead": lead, "deals": deals, "messages": messages, "profile": profile,
        })


@login_required
@require_POST
def lead_toggle_disqualify(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    lead.disqualified = not lead.disqualified
    lead.save(update_fields=["disqualified"])
    return redirect("crm:lead_detail", pk=pk)


# ---------------------------------------------------------------------------
# Deals
# ---------------------------------------------------------------------------

class DealListView(LoginRequiredMixin, View):
    def get(self, request):
        state = request.GET.get("state", "")
        campaign_id = request.GET.get("campaign", "")
        q = request.GET.get("q", "").strip()

        deals = Deal.objects.select_related("lead", "campaign").order_by("-update_date")
        if state:
            deals = deals.filter(state=state)
        if campaign_id:
            deals = deals.filter(campaign_id=campaign_id)
        if q:
            deals = deals.filter(
                Q(lead__first_name__icontains=q) | Q(lead__last_name__icontains=q)
                | Q(lead__company_name__icontains=q)
            )

        campaigns = Campaign.objects.all()
        states = [(s.value, s.value) for s in ProfileState]
        ctx = {
            "deals": deals[:200], "state": state, "campaigns": campaigns,
            "campaign_id": campaign_id, "states": states, "q": q,
        }
        if request.headers.get("HX-Request"):
            return render(request, "crm/partials/deal_table.html", ctx)
        return render(request, "crm/deals.html", ctx)


class DealDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        deal = get_object_or_404(Deal.objects.select_related("lead", "campaign"), pk=pk)
        messages = ChatMessage.objects.filter(
            content_type__model="lead", object_id=deal.lead_id
        ).order_by("creation_date")
        return render(request, "crm/deal_detail.html", {"deal": deal, "messages": messages})


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

class CampaignListView(LoginRequiredMixin, View):
    def get(self, request):
        campaigns = Campaign.objects.annotate(
            deal_count=Count("deals"),
            user_count=Count("users"),
        ).order_by("name")
        return render(request, "crm/campaigns.html", {"campaigns": campaigns})


class CampaignDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        campaign = get_object_or_404(Campaign, pk=pk)
        deals = Deal.objects.filter(campaign=campaign).select_related("lead").order_by("-update_date")
        keywords = SearchKeyword.objects.filter(campaign=campaign).order_by("-used", "keyword")
        state_counts = {}
        for s in ProfileState:
            state_counts[s.value] = deals.filter(state=s.value).count()
        return render(request, "crm/campaign_detail.html", {
            "campaign": campaign, "deals": deals[:100], "keywords": keywords,
            "state_counts": state_counts,
        })


class CampaignEditView(LoginRequiredMixin, View):
    def get(self, request, pk):
        campaign = get_object_or_404(Campaign, pk=pk)
        return render(request, "crm/campaign_edit.html", {"campaign": campaign})

    def post(self, request, pk):
        campaign = get_object_or_404(Campaign, pk=pk)
        campaign.name = request.POST.get("name", campaign.name)
        campaign.product_docs = request.POST.get("product_docs", campaign.product_docs)
        campaign.campaign_objective = request.POST.get("campaign_objective", campaign.campaign_objective)
        campaign.booking_link = request.POST.get("booking_link", campaign.booking_link)
        campaign.is_freemium = request.POST.get("is_freemium") == "on"
        try:
            campaign.action_fraction = float(request.POST.get("action_fraction", campaign.action_fraction))
        except (ValueError, TypeError):
            pass
        campaign.save()
        return redirect("crm:campaign_detail", pk=pk)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

class TaskListView(LoginRequiredMixin, View):
    def get(self, request):
        status = request.GET.get("status", "")
        task_type = request.GET.get("type", "")
        tasks = Task.objects.all().order_by("-created_at")
        if status:
            tasks = tasks.filter(status=status)
        if task_type:
            tasks = tasks.filter(task_type=task_type)
        ctx = {"tasks": tasks[:200], "status": status, "task_type": task_type}
        if request.headers.get("HX-Request"):
            return render(request, "crm/partials/task_table.html", ctx)
        return render(request, "crm/tasks.html", ctx)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsView(LoginRequiredMixin, View):
    def get(self, request):
        cfg = SiteConfig.load()
        profiles = LinkedInProfile.objects.select_related("user").all()
        return render(request, "crm/settings.html", {"cfg": cfg, "profiles": profiles})

    def post(self, request):
        cfg = SiteConfig.load()
        cfg.llm_provider = request.POST.get("llm_provider", cfg.llm_provider)
        cfg.ai_model = request.POST.get("ai_model", cfg.ai_model)
        cfg.llm_api_key = request.POST.get("llm_api_key", cfg.llm_api_key)
        cfg.llm_api_base = request.POST.get("llm_api_base", cfg.llm_api_base)
        cfg.save()
        profiles = LinkedInProfile.objects.select_related("user").all()
        return render(request, "crm/settings.html", {
            "cfg": cfg, "profiles": profiles, "saved": True,
        })


# ---------------------------------------------------------------------------
# Activity Log
# ---------------------------------------------------------------------------

class ActivityLogView(LoginRequiredMixin, View):
    def get(self, request):
        logs = ActionLog.objects.select_related(
            "linkedin_profile__user", "campaign"
        ).order_by("-created_at")[:200]
        return render(request, "crm/activity.html", {"logs": logs})


# ---------------------------------------------------------------------------
# Accounts & Session Control
# ---------------------------------------------------------------------------

class AccountsView(LoginRequiredMixin, View):
    """LinkedIn profiles dashboard with daemon start/stop controls."""

    def get(self, request):
        from linkedin.daemon_manager import get_all_daemons

        profiles = LinkedInProfile.objects.select_related("user").all()
        daemons = get_all_daemons()

        accounts = []
        for p in profiles:
            daemon_info = daemons.get(p.pk)
            campaigns = Campaign.objects.filter(users=p.user)
            daily_connects = ActionLog.objects.filter(
                linkedin_profile=p, action_type="connect",
                created_at__date=timezone.now().date(),
            ).count()
            daily_followups = ActionLog.objects.filter(
                linkedin_profile=p, action_type="follow_up",
                created_at__date=timezone.now().date(),
            ).count()
            accounts.append({
                "profile": p,
                "campaigns": campaigns,
                "daemon_state": daemon_info.state.value if daemon_info else "stopped",
                "daemon_error": daemon_info.error if daemon_info else "",
                "daemon_started_at": daemon_info.started_at if daemon_info else None,
                "daemon_campaigns": daemon_info.campaign_names if daemon_info else [],
                "daily_connects": daily_connects,
                "daily_followups": daily_followups,
                "has_session": bool(p.cookie_data),
            })

        return render(request, "crm/accounts.html", {"accounts": accounts})


@login_required
@require_POST
def daemon_start(request, profile_pk):
    from linkedin.daemon_manager import start_daemon
    success, msg = start_daemon(profile_pk)
    from django.contrib import messages
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("crm:accounts")


@login_required
@require_POST
def daemon_stop(request, profile_pk):
    from linkedin.daemon_manager import stop_daemon
    success, msg = stop_daemon(profile_pk)
    from django.contrib import messages
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("crm:accounts")


@login_required
def daemon_status_api(request):
    """JSON endpoint for polling daemon statuses via HTMX/JS."""
    from linkedin.daemon_manager import get_all_daemons
    daemons = get_all_daemons()
    data = {}
    for pk, info in daemons.items():
        data[pk] = {
            "state": info.state.value,
            "started_at": info.started_at.isoformat() if info.started_at else None,
            "error": info.error,
            "campaigns": info.campaign_names,
        }
    return JsonResponse(data)


# ---------------------------------------------------------------------------
# Profile CRUD (Add / Edit LinkedIn Accounts)
# ---------------------------------------------------------------------------

class ProfileCreateView(LoginRequiredMixin, View):
    def get(self, request):
        campaigns = Campaign.objects.all()
        return render(request, "crm/profile_create.html", {"campaigns": campaigns})

    def post(self, request):
        from django.contrib import messages

        username = request.POST.get("username", "").strip()
        linkedin_username = request.POST.get("linkedin_username", "").strip()
        linkedin_password = request.POST.get("linkedin_password", "").strip()

        if not username or not linkedin_username or not linkedin_password:
            messages.error(request, "All fields are required.")
            campaigns = Campaign.objects.all()
            return render(request, "crm/profile_create.html", {"campaigns": campaigns})

        if User.objects.filter(username=username).exists():
            messages.error(request, f"User '{username}' already exists.")
            campaigns = Campaign.objects.all()
            return render(request, "crm/profile_create.html", {"campaigns": campaigns})

        user = User.objects.create_user(username=username, password=username)
        profile = LinkedInProfile.objects.create(
            user=user,
            linkedin_username=linkedin_username,
            linkedin_password=linkedin_password,
            connect_daily_limit=int(request.POST.get("connect_daily_limit", 20)),
            connect_weekly_limit=int(request.POST.get("connect_weekly_limit", 100)),
            follow_up_daily_limit=int(request.POST.get("follow_up_daily_limit", 30)),
            active=True,
            legal_accepted=request.POST.get("legal_accepted") == "on",
        )

        # Assign campaigns
        campaign_ids = request.POST.getlist("campaigns")
        if campaign_ids:
            for cid in campaign_ids:
                try:
                    campaign = Campaign.objects.get(pk=int(cid))
                    campaign.users.add(user)
                except (Campaign.DoesNotExist, ValueError):
                    pass

        messages.success(request, f"Profile '{profile}' created.")
        return redirect("crm:accounts")


class ProfileEditView(LoginRequiredMixin, View):
    def get(self, request, pk):
        profile = get_object_or_404(LinkedInProfile.objects.select_related("user"), pk=pk)
        campaigns = Campaign.objects.all()
        assigned_ids = list(
            Campaign.objects.filter(users=profile.user).values_list("pk", flat=True)
        )
        return render(request, "crm/profile_edit.html", {
            "profile": profile, "campaigns": campaigns, "assigned_ids": assigned_ids,
        })

    def post(self, request, pk):
        from django.contrib import messages

        profile = get_object_or_404(LinkedInProfile.objects.select_related("user"), pk=pk)

        profile.linkedin_username = request.POST.get("linkedin_username", profile.linkedin_username)
        profile.linkedin_password = request.POST.get("linkedin_password", profile.linkedin_password)
        profile.active = request.POST.get("active") == "on"
        profile.legal_accepted = request.POST.get("legal_accepted") == "on"

        try:
            profile.connect_daily_limit = int(request.POST.get("connect_daily_limit", profile.connect_daily_limit))
            profile.connect_weekly_limit = int(request.POST.get("connect_weekly_limit", profile.connect_weekly_limit))
            profile.follow_up_daily_limit = int(request.POST.get("follow_up_daily_limit", profile.follow_up_daily_limit))
        except (ValueError, TypeError):
            pass

        profile.save()

        # Update campaign assignments
        campaign_ids = request.POST.getlist("campaigns")
        # Remove from all campaigns first
        for c in Campaign.objects.filter(users=profile.user):
            c.users.remove(profile.user)
        # Add to selected campaigns
        for cid in campaign_ids:
            try:
                campaign = Campaign.objects.get(pk=int(cid))
                campaign.users.add(profile.user)
            except (Campaign.DoesNotExist, ValueError):
                pass

        messages.success(request, f"Profile '{profile}' updated.")
        return redirect("crm:accounts")


# ---------------------------------------------------------------------------
# Campaign Create
# ---------------------------------------------------------------------------

class CampaignCreateView(LoginRequiredMixin, View):
    def get(self, request):
        profiles = LinkedInProfile.objects.select_related("user").filter(active=True)
        return render(request, "crm/campaign_create.html", {"profiles": profiles})

    def post(self, request):
        from django.contrib import messages

        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Campaign name is required.")
            profiles = LinkedInProfile.objects.select_related("user").filter(active=True)
            return render(request, "crm/campaign_create.html", {"profiles": profiles})

        if Campaign.objects.filter(name=name).exists():
            messages.error(request, f"Campaign '{name}' already exists.")
            profiles = LinkedInProfile.objects.select_related("user").filter(active=True)
            return render(request, "crm/campaign_create.html", {"profiles": profiles})

        campaign = Campaign.objects.create(
            name=name,
            product_docs=request.POST.get("product_docs", ""),
            campaign_objective=request.POST.get("campaign_objective", ""),
            booking_link=request.POST.get("booking_link", ""),
            is_freemium=request.POST.get("is_freemium") == "on",
        )
        try:
            campaign.action_fraction = float(request.POST.get("action_fraction", 0.2))
        except (ValueError, TypeError):
            pass
        campaign.save()

        # Assign users from selected profiles
        profile_ids = request.POST.getlist("profiles")
        for pid in profile_ids:
            try:
                profile = LinkedInProfile.objects.select_related("user").get(pk=int(pid))
                campaign.users.add(profile.user)
            except (LinkedInProfile.DoesNotExist, ValueError):
                pass

        messages.success(request, f"Campaign '{campaign.name}' created.")
        return redirect("crm:campaign_detail", pk=campaign.pk)
