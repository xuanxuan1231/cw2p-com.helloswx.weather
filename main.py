"""
天气
A Class Widgets plugin.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from ClassWidgets.SDK import CW2Plugin, PluginAPI
from PySide6.QtCore import Property, QThread, QTimer, Signal, Slot
from PySide6.QtQml import QJSValue
from loguru import logger

from weather import geolocate, service
from weather.cities import built_in_cities
from weather.config import WeatherConfig
from weather.models import CITY, COORDINATES
from weather.providers import get_provider_class

WIDGET_ID = "helloswx.weather"
WIDGET_DEFAULTS = {
    # 空字符串表示始终跟随插件的默认城市
    "city_id": "",
    # current | high_low —— 主体屏幕显示实时温度还是当日最高 / 最低温
    "content_mode": "current",
    # 额外加入「未来 3 小时」屏幕
    "show_hourly": False,
    # 主体与次级屏幕之间轮播
    "carousel": True,
    # 主体屏幕停留时长（优先级最高，停留更久）
    "main_seconds": 12,
    # 降水预报 / 天气预警屏幕停留时长
    "detail_seconds": 6,
}


def _qml_to_python(value: Any) -> Any:
    """Convert QML object arguments (QJSValue) into Python containers.

    PySide6 may deliver an inline QML object passed to a ``QVariant`` slot as
    ``QJSValue`` instead of ``dict``.  Keep this conversion at the API
    boundary so the rest of the weather/config code can stay type-agnostic.
    """
    if not isinstance(value, QJSValue):
        if isinstance(value, dict):
            return {str(key): _qml_to_python(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_qml_to_python(item) for item in value]
        return value

    converted = value.toVariant()
    if converted is not None and converted is not value and not isinstance(converted, QJSValue):
        return _qml_to_python(converted)
    if value.isArray():
        length = int(value.property("length").toVariant() or 0)
        return [_qml_to_python(value.property(str(index))) for index in range(length)]
    if value.isObject():
        # QJSValue does not expose enumerable keys consistently across Qt
        # versions; these are the keys accepted by addCityRecord().
        keys = ("id", "name", "code", "mode", "latitude", "longitude", "provider_codes")
        return {
            key: _qml_to_python(value.property(key))
            for key in keys
            if value.hasProperty(key)
        }
    return None



class SearchWorker(QThread):
    """在线城市搜索（Open-Meteo 地理编码）。"""

    finishedWith = Signal(str, list)

    def __init__(self, provider, term: str, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._term = term

    def run(self) -> None:
        try:
            results = self._provider.search_cities(self._term)
        except Exception:
            results = []
        self.finishedWith.emit(self._term, results)


class LocateWorker(QThread):
    """按 IP 粗定位，并折算成当前数据源可用的位置。"""

    finishedWith = Signal(dict)

    def __init__(self, provider_id: str, parent=None):
        super().__init__(parent)
        self._provider_id = provider_id

    def run(self) -> None:
        resolved: Dict[str, Any] = {}
        try:
            fix = geolocate.locate()
            if fix:
                resolved = service.resolve_auto_location(self._provider_id, fix) or {}
        except Exception:
            resolved = {}
        self.finishedWith.emit(resolved)


class Plugin(CW2Plugin):
    dataChanged = Signal()
    configChanged = Signal()
    citiesFound = Signal(str, "QVariant")
    locatingChanged = Signal()

    def __init__(self, api: PluginAPI):
        super().__init__(api)
        self.config = WeatherConfig()
        service.migrate_cities(self.config)
        self._payload: Dict[str, Any] = {"available": False, "error": "正在获取天气…"}
        self._city_payloads: Dict[str, Dict[str, Any]] = {}
        self._fetch_workers: Dict[str, service.FetchWorker] = {}
        self._fetch_worker: Optional[service.FetchWorker] = None
        self._search_worker: Optional[SearchWorker] = None
        self._locate_worker: Optional[LocateWorker] = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._settings_page = Path("qml") / "SettingsPage.qml"

    # ------------------------------------------------------------------ 生命周期

    def on_load(self):
        super().on_load()

        self.api.config.register_plugin_model(self.pid, self.config)
        service.migrate_cities(self.config)
        service.sync_legacy_location(
            self.config,
            next((city for city in self.config.cities if city["id"] == self.defaultCityId()), None),
        )
        self.api.config.save()

        self.api.widgets.register(
            widget_id=WIDGET_ID,
            name="天气",
            qml_path=Path("qml") / "WeatherWidget.qml",
            backend_obj=self,
            settings_qml=Path("qml") / "WidgetSettings.qml",
            default_settings=dict(WIDGET_DEFAULTS),
        )
        self.api.ui.register_settings_page(
            self._settings_page,
            title="天气",
            icon="ic_fluent_weather_partly_cloudy_day_20_regular",
        )

        self._restart_timer()
        # 首次安装（从未定过位）时按 IP 自动选一次城市，之后一律手动
        if not self.config.located_once and not self.config.cities:
            self.locateAutomatically()
        else:
            self.refresh()

    def on_unload(self):
        self._timer.stop()
        for worker in tuple(self._fetch_workers.values()) + (self._fetch_worker, self._search_worker, self._locate_worker):
            if worker is not None and worker.isRunning():
                worker.quit()
                worker.wait(3000)
        try:
            self.api.ui.unregister_settings_page(self._settings_page)
        except Exception:
            pass

    def _restart_timer(self):
        minutes = max(5, int(self.config.refresh_minutes or 5))
        self._timer.start(minutes * 60 * 1000)

    # ------------------------------------------------------------------ 天气数据

    @Property("QVariant", notify=dataChanged)
    def data(self) -> Dict[str, Any]:
        return self._payload

    def _cancel_fetch_worker(self) -> None:
        """取消正在运行的天气获取任务。"""
        workers = list(self._fetch_workers.values())
        if self._fetch_worker is not None and self._fetch_worker not in workers:
            workers.append(self._fetch_worker)
        for worker in workers:
            try:
                if worker.isRunning():
                    worker.finishedWith.disconnect()
                    worker.quit()
                    worker.wait(1000)
            except RuntimeError:
                pass  # C++ 对象已被删除
        self._fetch_workers.clear()
        self._fetch_worker = None

    @Slot()
    def refresh(self) -> None:
        """异步刷新所有城市；``data`` 始终代表默认城市。"""
        cities = service.migrate_cities(self.config)
        provider = service.make_provider(self.config)
        if provider is None:
            self._apply_for_city(None, service.build_payload(None, service.location_from_config(self.config), None, "天气源不可用"))
            return
        if provider.requires_key and not provider.api_key:
            self._apply_for_city(None, service.build_payload(None, service.location_from_config(self.config), provider, "未填写 API Key"))
            return
        if not cities:
            self._apply_for_city(None, service.build_payload(None, service.location_from_config(self.config), provider, "未选择城市"))
            return
        for city in cities:
            city_id = str(city["id"])
            if city_id in self._fetch_workers:
                continue
            location = service.location_from_city(city, self.config.provider)
            if not location.configured:
                self._apply_for_city(city_id, service.build_payload(None, location, provider, "当前数据源没有该城市映射"))
                continue
            worker = service.FetchWorker(provider, location, self)
            worker.finishedWith.connect(lambda payload, cid=city_id: self._apply_for_city(cid, payload))
            worker.finished.connect(worker.deleteLater)
            worker.finished.connect(lambda cid=city_id: self._clear_fetch_worker(cid))
            self._fetch_workers[city_id] = worker
            self._fetch_worker = worker
            worker.start()

    @Slot(dict)
    def _apply(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.dataChanged.emit()

    @Slot("QVariant", dict)
    def _apply_for_city(self, city_id: Optional[str], payload: Dict[str, Any]) -> None:
        if city_id:
            self._city_payloads[str(city_id)] = payload
        default_id = self.defaultCityId()
        self._payload = self._city_payloads.get(default_id, payload)
        self.dataChanged.emit()
    
    @Slot()
    def _clear_fetch_worker(self, city_id: str = "") -> None:
        """在 worker 完成后清空引用。"""
        if city_id:
            self._fetch_workers.pop(str(city_id), None)
        self._fetch_worker = next(iter(self._fetch_workers.values()), None)

    # ------------------------------------------------------------------ 设置页接口

    @Slot(result="QVariant")
    def providers(self) -> List[Dict[str, Any]]:
        result = service.describe_providers()
        logger.info(f"providers() called, returning {len(result)} providers: {[p['id'] for p in result]}")
        return result

    @Slot(result="QVariant")
    def settings(self) -> Dict[str, Any]:
        """当前配置 + 城市列表 + 当前提供商能力。"""
        logger.info("settings() called")
        provider_class = get_provider_class(self.config.provider)
        api_keys = self.config.api_keys or {}
        cities = self.cityOptions()
        default = next((item for item in cities if item["id"] == self.defaultCityId()), None)
        location = service.location_from_city(default, self.config.provider) if default else service.location_from_config(self.config)
        return {
            "provider": self.config.provider,
            "providerName": provider_class.name if provider_class else "",
            "requiresKey": bool(provider_class and provider_class.requires_key),
            "supportsCoordinates": bool(provider_class and provider_class.supports_coordinates),
            "hasCityList": bool(provider_class and provider_class.database),
            "onlineSearch": bool(provider_class and not provider_class.database),
            "locationMode": location.mode,
            "cityCode": location.code,
            "cityName": location.label,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "apiKey": api_keys.get(self.config.provider, ""),
            "refreshMinutes": self.config.refresh_minutes,
            "located": bool(default and location.configured),
            "error": self._payload.get("error", ""),
            "locating": self.locating,
            "cities": cities,
            "defaultCityId": self.defaultCityId(),
        }

    @Slot(result=str)
    def defaultCityId(self) -> str:
        cities = service.migrate_cities(self.config)
        if self.config.default_city_id and any(str(item["id"]) == self.config.default_city_id for item in cities):
            return self.config.default_city_id
        return str(cities[0]["id"]) if cities else ""

    def _city_options(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        cities = service.migrate_cities(self.config)
        for city in cities:
            item = service.normalize_city(city)
            resolved = service.city_for_provider(item, self.config.provider)
            payload = self._city_payloads.get(item["id"], {})
            item.update({
                "provider": self.config.provider,
                "providerCode": (resolved or {}).get("code", ""),
                "available": bool(payload.get("available")),
                "temperature": payload.get("temperature", ""),
                "description": payload.get("description", ""),
                "error": payload.get("error", ""),
                "isDefault": item["id"] == self.defaultCityId(),
                "canRemove": len(cities) > 1,
            })
            result.append(item)
        return result

    @Slot(result="QVariant")
    def cityOptions(self) -> List[Dict[str, Any]]:
        return self._city_options()

    @Slot(str, result="QVariant")
    def dataForCity(self, city_id: str) -> Dict[str, Any]:
        return self._city_payloads.get(str(city_id), self._payload)

    @Slot(str)
    def setProvider(self, provider_id: str) -> None:
        """切换提供商，保留规范城市并尽可能建立新的 provider 映射。"""
        if not provider_id or provider_id == self.config.provider:
            return
        if get_provider_class(provider_id) is None:
            return

        self.config.provider = provider_id
        self.config.located_once = True
        mapped = []
        for city in service.migrate_cities(self.config):
            resolved = service.city_for_provider(city, provider_id)
            if resolved:
                city = service.normalize_city(resolved, provider_id)
            mapped.append(city)
        self.config.cities = mapped
        service.sync_legacy_location(self.config, next((c for c in mapped if c["id"] == self.defaultCityId()), None))

        self.api.config.save()
        self.configChanged.emit()
        # 立即触发 dataChanged 确保前端能获取到最新的 requiresKey 状态
        self.dataChanged.emit()
        self.refresh()

    @Slot(str, str)
    def setApiKey(self, provider_id: str, key: str) -> None:
        keys = dict(self.config.api_keys or {})
        keys[provider_id or self.config.provider] = key or ""
        self.config.api_keys = keys
        self.api.config.save()
        self.configChanged.emit()
        self.refresh()

    @Slot(int)
    def setRefreshMinutes(self, minutes: int) -> None:
        self.config.refresh_minutes = max(5, int(minutes))
        self.api.config.save()
        self._restart_timer()
        self.configChanged.emit()

    @Slot(str, str, float, float)
    def setCity(self, code: str, name: str, latitude: float, longitude: float) -> None:
        """兼容旧调用：添加或更新一个城市并设为默认。"""
        city = service.normalize_city({
            "name": name, "code": code, "mode": CITY,
            "latitude": latitude or None, "longitude": longitude or None,
        }, self.config.provider)
        self.addCityRecord(city)

    @Slot("QVariant")
    def addCityRecord(self, city: Dict[str, Any]) -> None:
        city = _qml_to_python(city)
        if not isinstance(city, dict):
            logger.warning("addCityRecord() ignored a non-object city argument: %r", city)
            return
        self.config.located_once = True
        if not str(city.get("name") or "").strip() and city.get("latitude") is not None and city.get("longitude") is not None:
            try:
                city = dict(city)
                city["name"] = geolocate.city_name_at(float(city["latitude"]), float(city["longitude"]), timeout=5.0)
            except Exception:
                city["name"] = ""
        record = service.normalize_city(city, self.config.provider)
        existing = service.migrate_cities(self.config)
        duplicate = next((item for item in existing if item.get("name") == record.get("name") and item.get("mode") == record.get("mode")), None)
        if duplicate:
            record["id"] = duplicate["id"]
            merged_codes = dict(duplicate.get("provider_codes") or {})
            merged_codes.update(record.get("provider_codes") or {})
            record["provider_codes"] = merged_codes
            existing[existing.index(duplicate)] = record
        else:
            existing.append(record)
        self.config.cities = existing
        if not self.config.default_city_id:
            self.config.default_city_id = record["id"]
        default = next((item for item in existing if item["id"] == self.config.default_city_id), record)
        service.sync_legacy_location(self.config, default)
        self.api.config.save()
        self.configChanged.emit()
        self._cancel_fetch_worker()
        self.refresh()

    @Slot(str)
    def removeCity(self, city_id: str) -> None:
        current = service.migrate_cities(self.config)
        if len(current) <= 1:
            return
        cities = [item for item in current if str(item.get("id")) != str(city_id)]
        self.config.cities = cities
        if self.config.default_city_id == str(city_id):
            self.config.default_city_id = str(cities[0]["id"])
        service.sync_legacy_location(self.config, next(item for item in cities if item["id"] == self.defaultCityId()))
        self.api.config.save()
        self.configChanged.emit()
        self._cancel_fetch_worker()
        self.refresh()

    @Slot(str)
    def setDefaultCity(self, city_id: str) -> None:
        if not any(str(item.get("id")) == str(city_id) for item in service.migrate_cities(self.config)):
            return
        self.config.default_city_id = str(city_id)
        service.sync_legacy_location(self.config, next(item for item in self.config.cities if item["id"] == self.config.default_city_id))
        self.api.config.save()
        self.configChanged.emit()
        self._apply_for_city(self.config.default_city_id, self._city_payloads.get(self.config.default_city_id, self._payload))

    @Slot(float, float, str)
    def setCoordinates(self, latitude: float, longitude: float, name: str) -> None:
        """设置坐标位置；如果 name 为空，会尝试通过逆地理编码自动获取城市名称。"""
        # 如果用户没有提供名称，尝试自动获取
        if not name or not name.strip():
            try:
                name = geolocate.city_name_at(latitude, longitude, timeout=5.0)
            except Exception:
                name = ""
        
        self.addCityRecord({"name": name or f"{latitude:.2f}, {longitude:.2f}", "mode": COORDINATES,
                            "latitude": latitude, "longitude": longitude})

    # ------------------------------------------------------------------ 城市列表

    @Slot(result="QVariant")
    def provinces(self) -> List[str]:
        logger.info("provinces() called")
        repository = service.repository_for(self.config.provider)
        result = repository.provinces() if repository else []
        logger.info(f"  Returning {len(result)} provinces")
        return result

    @Slot(int, result="QVariant")
    def citiesIn(self, province_index: int) -> List[Dict[str, str]]:
        logger.info(f"citiesIn() called with province_index: {province_index}")
        repository = service.repository_for(self.config.provider)
        result = repository.cities(province_index) if repository else (built_in_cities() if province_index < 0 else [])
        logger.info(f"  Returning {len(result)} cities")
        if result:
            logger.info(f"  First city: {result[0]}")
        return result

    @Slot(str)
    def searchCities(self, term: str) -> None:
        """搜索城市，结果通过 :attr:`citiesFound` 返回。"""
        logger.info(f"searchCities() called with term: '{term}'")
        repository = service.repository_for(self.config.provider)
        if repository is not None:
            results = repository.search(term)
            logger.info(f"  Found {len(results)} results from repository")
            logger.info(f"  First result: {results[0] if results else 'N/A'}")
            logger.info(f"  Emitting citiesFound signal with term='{term}', results count={len(results)}")
            self.citiesFound.emit(term, results)
            logger.info(f"  citiesFound signal emitted successfully")
            return

        logger.info("  No repository available, checking for search worker")
        if self._search_worker is not None:
            try:
                if self._search_worker.isRunning():
                    logger.info("  Search worker already running, returning")
                    return
            except RuntimeError:
                self._search_worker = None
        provider = service.make_provider(self.config)
        if provider is None:
            logger.info("  No provider available, emitting empty results")
            self.citiesFound.emit(term, [])
            return

        logger.info("  Creating search worker")
        worker = SearchWorker(provider, term, self)
        worker.finishedWith.connect(self.citiesFound)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._clear_search_worker)
        self._search_worker = worker
        worker.start()
    
    @Slot()
    def _clear_search_worker(self) -> None:
        """在 worker 完成后清空引用。"""
        self._search_worker = None

    # ------------------------------------------------------------------ 自动定位

    @Property(bool, notify=locatingChanged)
    def locating(self) -> bool:
        if self._locate_worker is not None:
            try:
                return self._locate_worker.isRunning()
            except RuntimeError:
                self._locate_worker = None
                return False
        return False

    @Slot()
    def locateAutomatically(self) -> None:
        """按 IP 粗定位并写入配置。"""
        if self._locate_worker is not None:
            try:
                if self._locate_worker.isRunning():
                    return
            except RuntimeError:
                self._locate_worker = None

        worker = LocateWorker(self.config.provider, self)
        worker.finishedWith.connect(self._apply_location)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._clear_locate_worker)
        self._locate_worker = worker
        worker.start()
        self.locatingChanged.emit()

    @Slot(dict)
    def _apply_location(self, resolved: Dict[str, Any]) -> None:
        self.config.located_once = True
        if resolved:
            self.addCityRecord(resolved)
        else:
            self.api.config.save()
        self.locatingChanged.emit()
        self.configChanged.emit()
        if not resolved:
            self.refresh()
    
    @Slot()
    def _clear_locate_worker(self) -> None:
        """在 worker 完成后清空引用。"""
        self._locate_worker = None
        self.locatingChanged.emit()
