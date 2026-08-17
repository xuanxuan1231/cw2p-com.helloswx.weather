"""抓取编排：把提供商返回的 :class:`Snapshot` 整理成 QML 直接可用的数据。"""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from . import codes
from .cities import CityRepository
from .config import WeatherConfig
from .models import CITY, COORDINATES, Location, Snapshot
from .providers import ProviderError, WeatherProvider, create, get_provider_class, provider_classes

#: 分钟级降水最多提前多久提示
MINUTE_HORIZON = 120
#: 逐小时降水最多提前多久提示
HOUR_HORIZON = 12

_SEVERITY_RANK = {"red": 3, "orange": 2, "yellow": 1, "blue": 0}


def location_from_config(config: WeatherConfig) -> Location:
    code = config.city_code or ""
    name = ""
    mode = config.location_mode or CITY
    
    # 通过city_code查询城市名称
    if code and mode == CITY:
        repository = repository_for(config.provider)
        if repository is not None:
            name = repository.name_for_code(code)
    # 坐标模式下，优先使用保存的城市名称
    elif mode == COORDINATES and config.latitude is not None and config.longitude is not None:
        name = config.city_name or f"{config.latitude:.2f}, {config.longitude:.2f}"
    
    return Location(
        mode=mode,
        code=code,
        name=name,
        latitude=config.latitude,
        longitude=config.longitude,
    )


def _format_temperature(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{round(value):d}"


def _description_of(snapshot: Snapshot) -> str:
    """优先用数据源的中文描述，否则回落到归一化编号的中文名。"""
    description = (snapshot.description or "").strip()
    if description and any("一" <= character <= "鿿" for character in description):
        return description
    return codes.canonical_description(snapshot.canonical) or description


def _precipitation_screen(snapshot: Snapshot) -> Optional[Dict[str, Any]]:
    """计算「降水预报」屏幕。"""
    raining_now = codes.is_precipitation(snapshot.canonical)

    change_in: Optional[int] = None
    unit = ""
    upcoming = snapshot.canonical
    change_in_minutes: Optional[int] = None  # 保存实际分钟数用于判断

    series = snapshot.minutely
    if series and series.values:
        raining_now = series.values[0] > 0
        for index, value in enumerate(series.values):
            if (value > 0) != raining_now:
                minutes = index * series.step
                if 0 < minutes <= MINUTE_HORIZON:
                    change_in_minutes = minutes
                    # 1小时内：精确到分钟
                    if minutes <= 60:
                        change_in, unit = minutes, "分钟后"
                    # 1-2小时：模糊描述
                    elif minutes <= 90:
                        change_in, unit = 1.5, "小时后"
                    else:
                        change_in, unit = 2, "小时后"
                break

    # 只在分钟级数据无结果时才使用小时级数据，且限制在2小时内
    if change_in is None and snapshot.hourly:
        for index, entry in enumerate(snapshot.hourly):
            state = entry.precipitating
            if state is None:
                state = codes.is_precipitation(entry.canonical)
            if state != raining_now:
                hours = max(index, 1)
                if hours <= 2:  # 只显示2小时内的降水预报
                    change_in_minutes = hours * 60
                    # 1小时用模糊描述
                    if hours == 1:
                        change_in, unit = 1, "小时后"
                    # 2小时用模糊描述
                    else:
                        change_in, unit = 2, "小时后"
                    upcoming = entry.canonical
                break

    if change_in is None:
        return None

    starting = not raining_now
    snowing = upcoming in (13, 14, 15, 16, 17, 26, 27, 28, 34)
    
    # 生成标题和描述
    if starting:
        # 即将开始降水
        title = "降水预报"
    else:
        # 降水即将结束
        if change_in_minutes and change_in_minutes <= 60:
            # 距离结束1小时内，显示"X分钟内雨渐停"
            title = "降水预报"
            change_in, unit = change_in_minutes, "分钟内雨渐停" if not snowing else "分钟内雪渐停"
        else:
            # 距离结束1小时外，不显示
            return None
    
    slug = "snow" if snowing else "rain"

    colour, alpha = codes.GLOW_RAIN
    screen = {
        "active": True,
        "title": title,
        "value": str(change_in) if isinstance(change_in, int) else str(change_in),
        "unit": unit,
        "badgeIconPath": codes.icon_path(slug),
        "badgeIconScale": codes.icon_scale(slug),
        "glowColor": colour,
        "glowAlpha": alpha,
    }
    screen.update(codes.icon_visual(slug))
    return screen


def _alert_screen(snapshot: Snapshot) -> Optional[Dict[str, Any]]:
    if not snapshot.alerts:
        return None

    alert = sorted(
        snapshot.alerts,
        key=lambda item: _SEVERITY_RANK.get(item.get("severity", "yellow"), 1),
        reverse=True,
    )[0]

    severity = alert.get("severity", "yellow")
    colour, alpha = codes.SEVERITY_GLOW.get(severity, codes.SEVERITY_GLOW["yellow"])
    slug = codes.SEVERITY_ICON.get(severity, "code-yellow")
    screen = {
        "active": True,
        "title": alert.get("title", ""),
        "severity": severity,
        "text": alert.get("text", ""),
        "metrics": alert.get("metrics") or [],
        "badgeIconPath": codes.icon_path(slug),
        "badgeIconScale": codes.icon_scale(slug),
        "glowColor": colour,
        "glowAlpha": alpha,
    }
    screen.update(codes.icon_visual(slug))
    return screen


def build_payload(
    snapshot: Optional[Snapshot],
    location: Location,
    provider: Optional[WeatherProvider],
    error: str = "",
) -> Dict[str, Any]:
    """构造 QML 使用的数据字典。"""
    if snapshot is None:
        colour, alpha = codes.GLOW_UNAVAILABLE
        payload = {
            "available": False,
            "error": error or "天气数据不可用",
            "city": location.label,
            "description": "",
            "temperature": "",
            "glowColor": colour,
            "glowAlpha": alpha,
            "precipitation": {"active": False},
            "alert": {"active": False},
            "hourly": [],
        }
        payload.update(codes.icon_visual("code-yellow"))
        return payload

    colour, alpha = codes.glow_for(snapshot.canonical, snapshot.night)
    hourly: List[Dict[str, object]] = []
    for entry in snapshot.hourly[:3]:
        item: Dict[str, object] = {"temperature": _format_temperature(entry.temperature)}
        item.update(codes.icon_visual(codes.icon_slug(entry.canonical, entry.night)))
        hourly.append(item)

    payload = {
        "available": True,
        "error": "",
        "city": snapshot.city or location.label,
        "description": _description_of(snapshot),
        "temperature": _format_temperature(snapshot.temperature),
        "temperatureHigh": _format_temperature(snapshot.temp_high),
        "temperatureLow": _format_temperature(snapshot.temp_low),
        "night": snapshot.night,
        "glowColor": colour,
        "glowAlpha": alpha,
        "hourly": hourly,
        "precipitation": _precipitation_screen(snapshot) or {"active": False},
        "alert": _alert_screen(snapshot) or {"active": False},
    }
    payload.update(codes.icon_visual(codes.icon_slug(snapshot.canonical, snapshot.night)))
    return payload


class FetchWorker(QThread):
    """在后台线程抓取天气，完成后发出 :attr:`finishedWith`。"""

    finishedWith = Signal(dict)

    def __init__(self, provider: WeatherProvider, location: Location, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._location = location

    def run(self) -> None:
        try:
            snapshot = self._provider.fetch(self._location)
            payload = build_payload(snapshot, self._location, self._provider)
        except ProviderError as error:
            payload = build_payload(None, self._location, self._provider, str(error))
        except Exception as error:  # 提供商解析异常不应让线程崩溃
            payload = build_payload(None, self._location, self._provider, f"解析失败：{error}")
        self.finishedWith.emit(payload)


def describe_providers() -> List[Dict[str, Any]]:
    """给设置页用的提供商清单。"""
    return [
        {
            "id": provider_class.id,
            "name": provider_class.name,
            "requiresKey": provider_class.requires_key,
            "supportsCoordinates": provider_class.supports_coordinates,
            "supportsAlerts": provider_class.supports_alerts,
            "hasCityList": bool(provider_class.database),
            "onlineSearch": not provider_class.database,
        }
        for provider_class in provider_classes()
    ]


def repository_for(provider_id: str) -> Optional[CityRepository]:
    provider_class = get_provider_class(provider_id)
    if provider_class is None or not provider_class.database:
        return None
    return CityRepository(provider_class.database)


def make_provider(config: WeatherConfig) -> Optional[WeatherProvider]:
    return create(config.provider, api_key=(config.api_keys or {}).get(config.provider, ""))


def resolve_auto_location(provider_id: str, fix: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把 IP 粗定位结果落到当前数据源能用的位置上。

    有内置城市库的数据源要按城市名查出自己的编号；
    只认坐标的数据源直接用经纬度。都不行就返回 ``None``。
    """
    latitude = fix.get("latitude")
    longitude = fix.get("longitude")
    if latitude is None or longitude is None:
        return None

    name = str(fix.get("name") or "").strip()
    repository = repository_for(provider_id)
    if repository is not None and name:
        for candidate in (name, name.replace("市", "").replace("区", "")):
            matches = repository.search(candidate, limit=1)
            if matches:
                return {
                    "mode": CITY,
                    "code": matches[0]["code"],
                    "name": matches[0]["name"],
                    "latitude": latitude,
                    "longitude": longitude,
                }

    provider_class = get_provider_class(provider_id)
    if provider_class is not None and provider_class.supports_coordinates:
        return {
            "mode": COORDINATES,
            "code": "",
            "name": name or f"{latitude:.2f}, {longitude:.2f}",
            "latitude": latitude,
            "longitude": longitude,
        }
    return None
