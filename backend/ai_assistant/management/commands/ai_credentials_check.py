"""Offline credential/endpoint compatibility check; never contacts a provider."""

import json
from django.core.management.base import BaseCommand, CommandError
from ai_assistant import models as m
from ai_assistant.configuration import endpoint
from ai_assistant.secrets import decrypt


def validate(models, profiles, channels):
    checked = 0
    for row in [*models, *profiles]:
        endpoint(row.base_url)
        if not decrypt(row.api_key_encrypted):
            raise ValueError("Missing model credential")
        checked += 1
    for row in channels:
        for field in ("callback_token_encrypted", "callback_aes_key_encrypted"):
            if getattr(row, field):
                if not decrypt(getattr(row, field)):
                    raise ValueError("Empty callback credential")
                checked += 1
    return {"status": "verified", "credentialsChecked": checked, "providerCalls": 0}


class Command(BaseCommand):
    help = "Validate all current AI ciphertext and exact allowed model origins without paid calls."

    def handle(self, *args, **options):
        try:
            result = validate(
                m.AiModels.objects.all(),
                m.AiSpaceModelProfiles.objects.all(),
                m.AiChannels.objects.all(),
            )
        except Exception as error:
            raise CommandError(
                "AI 既有密文或模型来源与目标运行配置不兼容；未调用模型"
            ) from error
        self.stdout.write(json.dumps(result))
