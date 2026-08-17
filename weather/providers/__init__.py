"""天气提供商注册表。

导入各模块即可通过 ``@register`` 完成注册；顺序决定设置页中的展示顺序。
"""

from .base import (  # noqa: F401
    ProviderError,
    WeatherProvider,
    create,
    get_provider_class,
    provider_classes,
    register,
)
from . import xiaomi, qweather, amap, qq, open_meteo  # noqa: F401  保证注册副作用

__all__ = [
    "ProviderError",
    "WeatherProvider",
    "create",
    "get_provider_class",
    "provider_classes",
    "register",
]
