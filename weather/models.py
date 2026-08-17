"""插件内部使用的天气数据结构。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CITY = "city"
COORDINATES = "coordinates"


@dataclass
class Location:
    """用户选定的位置。"""

    mode: str = CITY
    code: str = ""
    name: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @property
    def configured(self) -> bool:
        if self.mode == COORDINATES:
            return self.latitude is not None and self.longitude is not None
        return bool(self.code) or (self.latitude is not None and self.longitude is not None)

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.mode == COORDINATES and self.configured:
            return f"{self.latitude:.2f}, {self.longitude:.2f}"
        return ""


@dataclass
class HourlyEntry:
    """逐小时预报的一项。"""

    canonical: int
    temperature: Optional[float] = None
    label: str = ""
    precipitating: Optional[bool] = None
    night: bool = False


@dataclass
class MinutelySeries:
    """分钟级降水序列。``step`` 为每项代表的分钟数。"""

    values: List[float]
    step: int = 1


@dataclass
class Snapshot:
    """一次抓取得到的全部天气信息。"""

    canonical: int = 99
    description: str = ""
    temperature: Optional[float] = None
    temp_high: Optional[float] = None
    temp_low: Optional[float] = None
    city: str = ""
    night: bool = False
    hourly: List[HourlyEntry] = field(default_factory=list)
    minutely: Optional[MinutelySeries] = None
    alerts: List[Dict[str, Any]] = field(default_factory=list)
