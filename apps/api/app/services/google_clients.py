import json

from google.cloud import storage
from google.oauth2 import service_account

from app.settings import Settings


def storage_client(settings: Settings) -> storage.Client:
    if settings.google_service_account_json:
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(settings.google_service_account_json)
        )
        return storage.Client(project=settings.google_cloud_project, credentials=credentials)
    if settings.google_application_credentials:
        return storage.Client.from_service_account_json(
            settings.google_application_credentials,
            project=settings.google_cloud_project,
        )
    return storage.Client(project=settings.google_cloud_project)
