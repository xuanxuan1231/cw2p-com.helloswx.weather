"""
天气
A Class Widgets plugin.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from ClassWidgets.SDK import CW2Plugin, PluginAPI
from PySide6.QtCore import Property, QThread, QTimer, Signal, Slot
from loguru import logger

from weather import geolocate, service
from weather.config import WeatherConfig
from weather.models import CITY, COORDINATES
from weather.providers import get_provider_class

WIDGET_ID = "helloswx.weather"
WIDGET_DEFAULTS = {
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
        self._payload: Dict[str, Any] = {"available": False, "error": "正在获取天气…"}
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
        if not self.config.located_once and not service.location_from_config(self.config).configured:
            self.locateAutomatically()
        else:
            self.refresh()

    def on_unload(self):
        self._timer.stop()
        for worker in (self._fetch_worker, self._search_worker, self._locate_worker):
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
        if self._fetch_worker is not None:
            try:
                if self._fetch_worker.isRunning():
                    self._fetch_worker.finishedWith.disconnect()
                    self._fetch_worker.quit()
                    self._fetch_worker.wait(1000)
            except RuntimeError:
                pass  # C++ 对象已被删除
            finally:
                self._fetch_worker = None

    @Slot()
    def refresh(self) -> None:
        """异步刷新天气；未配置位置时直接给出不可用状态。"""
        if self._fetch_worker is not None:
            try:
                if self._fetch_worker.isRunning():
                    return
            except RuntimeError:
                self._fetch_worker = None

        location = service.location_from_config(self.config)
        provider = service.make_provider(self.config)

        if provider is None:
            self._apply(service.build_payload(None, location, None, "天气源不可用"))
            return
        if provider.requires_key and not provider.api_key:
            self._apply(service.build_payload(None, location, provider, "未填写 API Key"))
            return
        if not location.configured:
            self._apply(service.build_payload(None, location, provider, "未选择城市"))
            return

        worker = service.FetchWorker(provider, location, self)
        worker.finishedWith.connect(self._apply)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._clear_fetch_worker)
        self._fetch_worker = worker
        worker.start()

    @Slot(dict)
    def _apply(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.dataChanged.emit()
    
    @Slot()
    def _clear_fetch_worker(self) -> None:
        """在 worker 完成后清空引用。"""
        self._fetch_worker = None

    # ------------------------------------------------------------------ 设置页接口

    @Slot(result="QVariant")
    def providers(self) -> List[Dict[str, Any]]:
        result = service.describe_providers()
        logger.info(f"providers() called, returning {len(result)} providers: {[p['id'] for p in result]}")
        return result

    @Slot(result="QVariant")
    def settings(self) -> Dict[str, Any]:
        """当前配置 + 当前提供商能力，供设置页与对话框使用。"""
        logger.info("settings() called")
        provider_class = get_provider_class(self.config.provider)
        api_keys = self.config.api_keys or {}
        
        # 通过city_code查询城市名称
        city_name = ""
        if self.config.city_code and self.config.location_mode == CITY:
            repository = service.repository_for(self.config.provider)
            if repository is not None:
                city_name = repository.name_for_code(self.config.city_code)
        # 坐标模式下，优先使用保存的城市名称
        elif self.config.location_mode == COORDINATES and self.config.latitude is not None and self.config.longitude is not None:
            city_name = self.config.city_name or f"{self.config.latitude:.2f}, {self.config.longitude:.2f}"
        
        return {
            "provider": self.config.provider,
            "providerName": provider_class.name if provider_class else "",
            "requiresKey": bool(provider_class and provider_class.requires_key),
            "supportsCoordinates": bool(provider_class and provider_class.supports_coordinates),
            "hasCityList": bool(provider_class and provider_class.database),
            "onlineSearch": bool(provider_class and not provider_class.database),
            "locationMode": self.config.location_mode or CITY,
            "cityCode": self.config.city_code or "",
            "cityName": city_name,
            "latitude": self.config.latitude,
            "longitude": self.config.longitude,
            "apiKey": api_keys.get(self.config.provider, ""),
            "refreshMinutes": self.config.refresh_minutes,
            "located": bool(service.location_from_config(self.config).configured),
            "error": self._payload.get("error", ""),
            "locating": self.locating,
        }

    @Slot(str)
    def setProvider(self, provider_id: str) -> None:
        """切换提供商，并清空已选位置以要求用户重新选择。"""
        if not provider_id or provider_id == self.config.provider:
            return
        if get_provider_class(provider_id) is None:
            return

        self.config.provider = provider_id
        self.config.located_once = True
        self.config.location_mode = CITY
        self.config.city_code = ""
        self.config.city_name = ""
        self.config.latitude = None
        self.config.longitude = None

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
        """保存城市选择；``latitude``/``longitude`` 为 0 表示无坐标信息。"""
        self.config.located_once = True
        self.config.location_mode = CITY
        self.config.city_code = code or ""
        self.config.city_name = ""  # 城市模式下清空 city_name，避免混淆
        self.config.latitude = latitude if latitude else None
        self.config.longitude = longitude if longitude else None
        self.api.config.save()
        self.configChanged.emit()
        self._cancel_fetch_worker()
        self.refresh()

    @Slot(float, float, str)
    def setCoordinates(self, latitude: float, longitude: float, name: str) -> None:
        """设置坐标位置；如果 name 为空，会尝试通过逆地理编码自动获取城市名称。"""
        # 如果用户没有提供名称，尝试自动获取
        if not name or not name.strip():
            try:
                name = geolocate.city_name_at(latitude, longitude, timeout=5.0)
            except Exception:
                name = ""
        
        self.config.located_once = True
        self.config.location_mode = COORDINATES
        self.config.city_code = ""
        self.config.city_name = name or ""
        self.config.latitude = latitude
        self.config.longitude = longitude
        self.api.config.save()
        self.configChanged.emit()
        self._cancel_fetch_worker()
        self.refresh()

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
        result = repository.cities(province_index) if repository else []
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
            self.config.location_mode = resolved.get("mode", CITY)
            self.config.city_code = resolved.get("code", "")
            self.config.city_name = resolved.get("name", "")
            self.config.latitude = resolved.get("latitude")
            self.config.longitude = resolved.get("longitude")
        self.api.config.save()
        self.locatingChanged.emit()
        self.configChanged.emit()
        self._cancel_fetch_worker()
        self.refresh()
    
    @Slot()
    def _clear_locate_worker(self) -> None:
        """在 worker 完成后清空引用。"""
        self._locate_worker = None
        self.locatingChanged.emit()
