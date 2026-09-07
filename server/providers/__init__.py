from .base import BaseProvider, Delta, ProviderError
from .openai_compat import OpenAICompatProvider

# 供应商类型注册表: 加新类型只需在这里加一行
PROVIDER_TYPES = {
    "openai_compat": OpenAICompatProvider,
}

__all__ = ["BaseProvider", "Delta", "ProviderError", "OpenAICompatProvider", "PROVIDER_TYPES"]
