from django.conf import settings
from django.db import models
from django.template.defaultfilters import truncatechars
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse


class ChatMessage(models.Model):

    class Meta:
        verbose_name = _("message")
        verbose_name_plural = _("messages")
        constraints = [
            models.UniqueConstraint(
                fields=["deal", "channel", "external_id"],
                name="uniq_deal_channel_external_id",
            ),
        ]

    class Channel(models.TextChoices):
        LINKEDIN = "linkedin", _("LinkedIn")
        EMAIL = "email", _("Email")

    deal = models.ForeignKey(
        "crm.Deal",
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name=_("Deal"),
    )

    content = models.TextField(
        blank=True, default='',
        verbose_name=_("Message")
    )    
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, blank=True, null=True, on_delete=models.CASCADE,
        verbose_name=_("Owner"),
        related_name="%(app_label)s_%(class)s_owner_related",
    )
    answer_to = models.ForeignKey(
        'self', blank=True, null=True, on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_answer_to_related",
        verbose_name=_("answer to")
    )
    topic = models.ForeignKey(
        'self', blank=True, null=True, on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_topic_related",
    )
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        verbose_name=_("recipients"),
        related_name="%(app_label)s_%(class)s_recipients_related",
    )
    to = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        verbose_name=_("to"),
        related_name="%(app_label)s_%(class)s_to_related",
    )
    creation_date = models.DateTimeField(
        default=timezone.now,
        verbose_name=_("Creation date")
    )
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.LINKEDIN,
        verbose_name=_("Channel"),
    )
    external_id = models.CharField(
        max_length=300,
        null=True,
        blank=True,
        verbose_name=_("External message id"),
        help_text=_("Voyager entityUrn (LinkedIn) or RFC-5322 Message-ID (email); per-deal dedup key"),
    )
    is_outgoing = models.BooleanField(
        default=True,
        verbose_name=_("Outgoing"),
        help_text=_("True if sent by us, False if received"),
    )
    def __str__(self):
        return f'{truncatechars(self.content, 70)}'

    def get_absolute_url(self):
        return reverse(f'admin:chat_{self._meta.model_name}_change', args=[str(self.id)])
