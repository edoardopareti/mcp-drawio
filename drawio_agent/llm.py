from __future__ import annotations

from .config import Config


def create_llm_client(cfg: Config):
    """Return (client, model_or_deployment) for Azure OpenAI or OpenAI-compatible endpoints."""
    from openai import AzureOpenAI, OpenAI

    if cfg.llm_provider == "azure":
        if not cfg.azure_endpoint or not cfg.azure_deployment:
            raise RuntimeError("Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT in .env")
        if cfg.azure_use_entra_id:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            token_provider = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
            client = AzureOpenAI(azure_endpoint=cfg.azure_endpoint, azure_ad_token_provider=token_provider,
                                 api_version=cfg.azure_api_version)
        else:
            if not cfg.azure_api_key:
                raise RuntimeError("Set AZURE_OPENAI_API_KEY, or AZURE_USE_ENTRA_ID=true")
            client = AzureOpenAI(azure_endpoint=cfg.azure_endpoint, api_key=cfg.azure_api_key,
                                 api_version=cfg.azure_api_version)
        return client, cfg.azure_deployment

    if cfg.llm_provider == "openai":
        if not cfg.openai_api_key and not cfg.openai_base_url:
            raise RuntimeError("Set OPENAI_API_KEY (or OPENAI_BASE_URL for a local OpenAI-compatible server)")
        client = OpenAI(api_key=cfg.openai_api_key or "not-needed", base_url=cfg.openai_base_url or None)
        return client, cfg.openai_model

    raise RuntimeError("LLM_PROVIDER must be 'azure' or 'openai'")
