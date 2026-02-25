"""
Centralized configuration and secrets management.

Priority order for secrets:
  1. Azure Key Vault (production)
  2. Environment variables / .env file (local development fallback)

To use Key Vault locally: run `az login` once in your terminal.
To use .env locally: just set the variables in your .env file.
"""

import os
from dotenv import load_dotenv

# Load .env for local dev (no-op in production if file doesn't exist)
load_dotenv()

KEYVAULT_URL = os.getenv("AZURE_KEYVAULT_URL", "https://gov-grants-msu.vault.azure.net/")


def _init_keyvault():
    """
    Try to connect to Azure Key Vault using DefaultAzureCredential.
    Returns a SecretClient on success, or None if unavailable (local dev fallback).
    """
    if not KEYVAULT_URL:
        return None
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KEYVAULT_URL, credential=credential)
        # Lightweight connectivity check
        client.get_secret("DATABASE-URL")
        return client
    except Exception:
        # Key Vault unreachable or not authenticated — fall back to .env
        return None


def _get_secret(client, kv_name: str, env_name: str, fallback: str = None) -> str:
    """
    Fetch a secret from Key Vault if available, otherwise from environment.
    """
    if client:
        try:
            return client.get_secret(kv_name).value
        except Exception:
            pass
    return os.getenv(env_name, fallback)


class Settings:
    def __init__(self):
        self._kv = _init_keyvault()

        self.database_url = _get_secret(
            self._kv, "DATABASE-URL", "DATABASE_URL",
            fallback="sqlite:///./gov_grants.db"
        )
        self.azure_openai_api_key = _get_secret(
            self._kv, "AZURE-OPENAI-API-KEY", "AZURE_OPENAI_API_KEY"
        )
        self.azure_openai_endpoint = _get_secret(
            self._kv, "AZURE-OPENAI-ENDPOINT", "AZURE_OPENAI_ENDPOINT"
        )
        self.azure_openai_deployment = _get_secret(
            self._kv, "AZURE-OPENAI-DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT",
            fallback="gpt-4o"
        )
        self.secret_key = _get_secret(
            self._kv, "SECRET-KEY", "SECRET_KEY"
        )
        self.brave_search_api_key = _get_secret(
            self._kv, "BRAVE-SEARCH-API-KEY", "BRAVE_SEARCH_API_KEY"
        )

        source = "Azure Key Vault" if self._kv else ".env / environment variables"
        print(f"[config] Secrets loaded from: {source}")


# Singleton — imported everywhere
settings = Settings()
