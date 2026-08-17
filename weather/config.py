"""插件配置模型。"""

from typing import Dict, Optional

from ClassWidgets.SDK import ConfigBaseModel
from pydantic import Field


class WeatherConfig(ConfigBaseModel):
    """保存在 ``plugins.configs.cw2p-com.helloswx.weather`` 下的配置。"""

    #: 当前天气提供商 ID
    provider: str = "xiaomi_weather"
    #: ``city`` 或 ``coordinates``
    location_mode: str = "city"
    #: 城市代码（城市模式）
    city_code: str = ""
    #: 城市名称（坐标模式下保存逆地理编码得到的名称）
    city_name: str = ""
    #: 经纬度（坐标模式，或由在线搜索得到的城市坐标）
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: 各提供商的 API Key，切换提供商时不会互相覆盖
    api_keys: Dict[str, str] = Field(default_factory=dict)
    #: 自动刷新间隔（分钟）
    refresh_minutes: int = 5
    #: 是否已经定过位。首次安装才自动按 IP 定位；
    #: 切换数据源会清空城市但保留该标记，以便强制用户手动重选。
    located_once: bool = False
