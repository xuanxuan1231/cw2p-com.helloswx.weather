"""城市数据库查询（data/*.db 来自 Class Widgets）。"""

from typing import Any, Dict, List, Optional, Tuple

import cysqlite

from pypinyin import Style, lazy_pinyin

from . import DATA_DIR


# Used by coordinate-only providers so the add-city dialog is useful before
# the first online search. Database-backed providers still expose their full
# local list; these records also make provider switching deterministic.
MAJOR_CHINESE_CITIES = [
    ("北京", 39.9042, 116.4074), ("天津", 39.3434, 117.3616),
    ("上海", 31.2304, 121.4737), ("重庆", 29.5630, 106.5516),
    ("石家庄", 38.0428, 114.5149), ("太原", 37.8706, 112.5489),
    ("呼和浩特", 40.8426, 111.7492), ("沈阳", 41.8057, 123.4315),
    ("长春", 43.8171, 125.3235), ("哈尔滨", 45.8038, 126.5349),
    ("南京", 32.0603, 118.7969), ("杭州", 30.2741, 120.1551),
    ("合肥", 31.8206, 117.2272), ("福州", 26.0745, 119.2965),
    ("南昌", 28.6820, 115.8579), ("济南", 36.6512, 117.1201),
    ("郑州", 34.7466, 113.6254), ("武汉", 30.5928, 114.3055),
    ("长沙", 28.2282, 112.9388), ("广州", 23.1291, 113.2644),
    ("南宁", 22.8170, 108.3669), ("海口", 20.0440, 110.1999),
    ("成都", 30.5728, 104.0668), ("贵阳", 26.6470, 106.6302),
    ("昆明", 25.0389, 102.7183), ("拉萨", 29.6500, 91.1000),
    ("西安", 34.3416, 108.9398), ("兰州", 36.0611, 103.8343),
    ("西宁", 36.6171, 101.7782), ("银川", 38.4872, 106.2309),
    ("乌鲁木齐", 43.8256, 87.6168),
]
_MAJOR_COORDINATES = {name: (latitude, longitude) for name, latitude, longitude in MAJOR_CHINESE_CITIES}


def coordinates_for_city(name: str) -> Optional[Tuple[float, float]]:
    return _MAJOR_COORDINATES.get(_display_name(str(name or "")))


def built_in_cities() -> List[Dict[str, Any]]:
    return [
        _city_dict(name, "", None) | {"mode": "coordinates", "latitude": lat, "longitude": lon}
        for name, lat, lon in MAJOR_CHINESE_CITIES
    ]


def _display_name(raw: str) -> str:
    """``北京.海淀`` -> ``北京 海淀``。"""
    return raw.replace(".", " ")


def _pinyin(text: str) -> Tuple[str, str]:
    """返回 (全拼, 首字母缩写)，均小写无空格。例：'北京' -> ('beijing', 'bj')。"""
    clean = text.replace(" ", "").replace(".", "")
    full = "".join(lazy_pinyin(clean))
    abbr = "".join(lazy_pinyin(clean, style=Style.FIRST_LETTER))
    return full, abbr


def _city_dict(name: str, code: str, province_id: int | None = None) -> Dict[str, Any]:
    display = _display_name(name)
    full, abbr = _pinyin(name)
    d: Dict[str, Any] = {"name": display, "code": str(code), "pinyin": full, "pinyin_abbr": abbr}
    if province_id is not None:
        d["province_id"] = province_id
    coordinates = _MAJOR_COORDINATES.get(display)
    if coordinates:
        d["latitude"], d["longitude"] = coordinates
    return d


class CityRepository:
    """只读访问城市表，``citys(_id, province_id, name, city_num)``。"""

    def __init__(self, database: str):
        self.database = database
        self._path = DATA_DIR / database

    def _connect(self) -> Optional[cysqlite.Connection]:
        if not self._path.exists():
            return None
        # Use cysqlite's native read-only flag so the bundled database cannot
        # be modified by the plugin.
        return cysqlite.connect(str(self._path), flags=cysqlite.SQLITE_OPEN_READONLY)

    def provinces(self) -> List[str]:
        connection = self._connect()
        if connection is None:
            return []
        with connection:
            rows = connection.execute("SELECT name FROM provinces ORDER BY _id").fetchall()
        return [row[0] for row in rows]

    def cities(self, province_index: int) -> List[Dict[str, str]]:
        """``provinces._id`` 从 1 开始，``citys.province_id`` 从 0 开始。
        ``province_index < 0`` 时返回全部城市。"""
        connection = self._connect()
        if connection is None:
            return []
        with connection:
            if province_index < 0:
                rows = connection.execute(
                    "SELECT name, city_num, province_id FROM citys ORDER BY province_id, _id"
                ).fetchall()
                return [_city_dict(name, code, pid) for name, code, pid in rows]
            rows = connection.execute(
                "SELECT name, city_num FROM citys WHERE province_id = ? ORDER BY _id",
                (province_index,),
            ).fetchall()
        return [_city_dict(name, code) for name, code in rows]

    def search(self, term: str, limit: int = 60) -> List[Dict[str, str]]:
        term = term.strip()
        if not term:
            return []
        connection = self._connect()
        if connection is None:
            return []
        pattern = f"%{term.replace('市', '').replace('区', '')}%"
        with connection:
            rows = connection.execute(
                "SELECT name, city_num, province_id FROM citys WHERE name LIKE ? ORDER BY LENGTH(name), _id LIMIT ?",
                (pattern, limit),
            ).fetchall()
        return [{"name": _display_name(name), "code": str(code), "province_id": province_id} for name, code, province_id in rows]

    def name_for_code(self, code: str) -> str:
        if not code:
            return ""
        connection = self._connect()
        if connection is None:
            return ""
        with connection:
            row = connection.execute(
                "SELECT name FROM citys WHERE city_num = ? LIMIT 1", (str(code),)
            ).fetchone()
        return _display_name(row[0]) if row else ""
