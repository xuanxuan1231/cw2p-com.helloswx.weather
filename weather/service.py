"""抓取编排：把提供商返回的 :class:`Snapshot` 整理成 QML 直接可用的数据。"""

from typing import Any, Dict, List, Optional
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from . import codes
from .cities import CityRepository, coordinates_for_city
from .config import WeatherConfig
from .models import CITY, COORDINATES, Location, Snapshot
from .providers import ProviderError, WeatherProvider, create, get_provider_class, provider_classes

#: 分钟级降水最多提前多久提示
MINUTE_HORIZON = 120
#: 逐小时降水最多提前多久提示
HOUR_HORIZON = 12

_SEVERITY_RANK = {"red": 3, "orange": 2, "yellow": 1, "blue": 0}


def location_from_config(config: WeatherConfig) -> Location:
    if not (config.city_code or config.latitude is not None or config.longitude is not None):
        cities = getattr(config, "cities", None) or []
        default_id = getattr(config, "default_city_id", "")
        city = next((item for item in cities if str(item.get("id")) == str(default_id)), None) or (cities[0] if cities else None)
        if city:
            return location_from_city(city, config.provider)
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


def normalize_city(city: Dict[str, Any], provider_id: str = "") -> Dict[str, Any]:
    """Return a stable, JSON-friendly city record used across providers."""
    record = dict(city or {})
    record.setdefault("id", str(uuid4()))
    record["id"] = str(record["id"])
    record["name"] = str(record.get("name") or "").replace(".", " ")
    record["mode"] = str(record.get("mode") or (COORDINATES if not record.get("code") else CITY))
    if record.get("latitude") is not None:
        record["latitude"] = float(record["latitude"])
    if record.get("longitude") is not None:
        record["longitude"] = float(record["longitude"])
    if record.get("latitude") is None or record.get("longitude") is None:
        coordinates = coordinates_for_city(record["name"])
        if coordinates:
            record["latitude"], record["longitude"] = coordinates
    codes = dict(record.get("provider_codes") or {})
    if provider_id and record.get("code"):
        codes.setdefault(provider_id, str(record["code"]))
    record["provider_codes"] = {str(key): str(value) for key, value in codes.items() if value not in (None, "")}
    record.pop("code", None)
    return record


def migrate_cities(config: WeatherConfig) -> List[Dict[str, Any]]:
    """Migrate the legacy single-location fields into the city list once."""
    cities = [normalize_city(item, config.provider) for item in (config.cities or [])]
    if not cities and (config.city_code or config.city_name or config.latitude is not None):
        legacy_name = config.city_name
        if not legacy_name and config.city_code:
            repository = repository_for(config.provider)
            if repository:
                legacy_name = repository.name_for_code(config.city_code)
        cities = [normalize_city({
            "name": legacy_name,
            "code": config.city_code,
            "mode": config.location_mode,
            "latitude": config.latitude,
            "longitude": config.longitude,
        }, config.provider)]
    config.cities = cities
    if cities and (not config.default_city_id or config.default_city_id not in {item["id"] for item in cities}):
        config.default_city_id = cities[0]["id"]
    return cities


def city_for_provider(city: Dict[str, Any], provider_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a normalized city to the current provider's location fields."""
    record = normalize_city(city)
    provider_class = get_provider_class(provider_id)
    has_coordinates = record.get("latitude") is not None and record.get("longitude") is not None
    if record["mode"] == COORDINATES and provider_class and provider_class.supports_coordinates and has_coordinates:
        return record
    code = (record.get("provider_codes") or {}).get(provider_id, "")
    if not code and provider_id:
        repository = repository_for(provider_id)
        if repository and record.get("name"):
            matches = repository.search(record["name"], limit=1)
            if matches:
                code = matches[0]["code"]
                record["provider_codes"][provider_id] = str(code)
    if not code:
        if provider_class and provider_class.supports_coordinates and has_coordinates:
            record["mode"] = COORDINATES
            return record
        return None
    record["mode"] = CITY
    record["code"] = str(code)
    return record


def location_from_city(city: Dict[str, Any], provider_id: str) -> Location:
    resolved = city_for_provider(city, provider_id)
    if not resolved:
        return Location(name=str((city or {}).get("name") or ""))
    return Location(
        mode=str(resolved.get("mode") or CITY),
        code=str(resolved.get("code") or ""),
        name=str(resolved.get("name") or ""),
        latitude=resolved.get("latitude"),
        longitude=resolved.get("longitude"),
    )


def sync_legacy_location(config: WeatherConfig, city: Optional[Dict[str, Any]]) -> None:
    """Keep legacy fields usable for older plugin/widget code and upgrades."""
    if not city:
        config.location_mode = CITY
        config.city_code = ""
        config.city_name = ""
        config.latitude = None
        config.longitude = None
        return
    resolved = city_for_provider(city, config.provider) or normalize_city(city)
    config.location_mode = str(resolved.get("mode") or CITY)
    config.city_code = str(resolved.get("code") or "")
    config.city_name = str(resolved.get("name") or "")
    config.latitude = resolved.get("latitude")
    config.longitude = resolved.get("longitude")


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
