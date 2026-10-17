"""城市数据库查询（data/*.db 来自 Class Widgets）。

GUI 线程上绝不能做的事都集中在这里，所以查询分成两类：

* **轻量查询**（``provinces`` / ``search_by_name`` / ``name_for_code`` / ``popular``）
  只走一次 SQL，几毫秒以内，可以在主线程直接调用；
* **重量查询**（``cities`` / ``search``）要读全库 + 拼音表（每个库几千条），
  第一次十几毫秒，之后才是纯 Python 过滤，因此只允许在工作线程里调用。

每个城市库的索引在进程内只建一次，多个线程共用（:class:`_CityIndex` 内部
带锁）。
"""

import json
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

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

#: 单次搜索返回的结果上限
SEARCH_LIMIT = 60
#: 单个省份列表返回的结果上限
PROVINCE_CITY_LIMIT = 300
#: 「按名字找城市」结果的缓存条目上限
_NAME_CACHE_LIMIT = 256
#: 离线生成的拼音表，见 tools/build_city_pinyin.py
PINYIN_TABLE = DATA_DIR / "city_pinyin.json"

_pinyin_tables: Dict[str, Dict[str, str]] = {}
_pinyin_tables_lock = threading.Lock()
_converter: Any = None
_converter_lock = threading.Lock()


def _pinyin_table(database: str) -> Dict[str, str]:
    """读离线生成的拼音表（城市名 -> 空格分隔的音节）。

    运行时用 pypinyin 现算要 ~300ms 的纯 Python 计算，会把 GIL 占住，GUI 线程
    跟着卡一下；表在构建期算好，这里只剩一次 json 解析。
    """
    with _pinyin_tables_lock:
        table = _pinyin_tables.get(database)
        if table is None:
            try:
                tables = json.loads(PINYIN_TABLE.read_text(encoding="utf-8"))
                table = tables.get(database) or {}
            except (OSError, ValueError) as error:
                logger.warning(f"拼音表不可用（{PINYIN_TABLE.name}: {error}），改用 pypinyin 现算")
                table = {}
            _pinyin_tables[database] = table
    return table


def _pinyin_engine() -> Any:
    """兜底用的 pypinyin 转换器，只有拼音表缺名字时才会真的导入它。

    ``import pypinyin`` 要执行两份大词典，~190ms 的 C 层 dict 构造，抢 GIL 很凶，
    所以它既不能出现在插件导入路径上，也不该出现在用户正在输入的时候。
    """
    global _converter
    if _converter is None:
        with _converter_lock:
            if _converter is None:
                from pypinyin.core import Pinyin

                _converter = Pinyin()
    return _converter


def _pinyin_syllables(name: str) -> List[str]:
    return list(_pinyin_engine().lazy_pinyin(_plain(name)))


def coordinates_for_city(name: str) -> Optional[Tuple[float, float]]:
    return _MAJOR_COORDINATES.get(_display_name(str(name or "")))


def built_in_cities() -> List[Dict[str, Any]]:
    """坐标型数据源的内置城市（直辖市 + 省会）。

    这里**不能**算拼音：坐标型数据源的城市名来自在线搜索，本地这份只是
    「空搜索时的推荐列表」，而 pypinyin 的导入要 ~190ms 的 GIL 时间。
    """
    return [
        {
            "name": name,
            "code": "",
            "pinyin": "",
            "pinyin_abbr": "",
            "mode": "coordinates",
            "latitude": latitude,
            "longitude": longitude,
        }
        for name, latitude, longitude in MAJOR_CHINESE_CITIES
    ]


def _display_name(raw: str) -> str:
    """``北京.海淀`` -> ``北京 海淀``。"""
    return raw.replace(".", " ")


def _plain(text: str) -> str:
    return text.replace(" ", "").replace(".", "")


def _build_index_rows(rows: List[Tuple[str, str, int, int]]) -> List[Dict[str, Any]]:
    """把 SQL 行变成内存索引；拼音留给 :meth:`_CityIndex.searchable` 补齐。"""
    return [
        {
            "name": _display_name(name),
            "code": str(code),
            "province_id": int(province_id),
            "order": int(order),
            **_coordinates_of(name),
        }
        for name, code, province_id, order in rows
    ]


def _coordinates_of(name: str) -> Dict[str, float]:
    coordinates = _MAJOR_COORDINATES.get(_display_name(name))
    return {"latitude": coordinates[0], "longitude": coordinates[1]} if coordinates else {}


class _CityIndex:
    """单个城市库的内存索引，建一次、多次复用。

    ``_lock`` 是可重入的：建索引的临界区里还会调用 :meth:`_ensure_rows`，
    而建索引本身是「检查缓存 → 构建 → 写回」，不可重入锁会死锁。
    """

    def __init__(self, path: Any, database: str):
        self._path = path
        self._database = database
        self._lock = threading.RLock()
        self._provinces: Optional[List[str]] = None
        self._rows: Optional[List[Dict[str, Any]]] = None
        self._searchable: Optional[List[Dict[str, Any]]] = None
        self._by_name: Dict[str, Dict[str, Any]] = {}
        self._by_code: Dict[str, Dict[str, Any]] = {}
        self._name_lookup: Dict[str, str] = {}
        self._name_cache: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}

    @property
    def available(self) -> bool:
        return self._path.exists()

    def _query(self, sql: str, parameters: Tuple[Any, ...] = ()) -> List[Tuple[Any, ...]]:
        connection = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        try:
            with connection:
                return connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

    # ------------------------------------------------------------ 轻量查询

    def provinces(self) -> List[str]:
        with self._lock:
            if self._provinces is None:
                self._provinces = [row[0] for row in self._query("SELECT name FROM provinces ORDER BY _id")]
            return self._provinces

    def name_for_code(self, code: str) -> str:
        if not code:
            return ""
        with self._lock:
            if self._rows is not None:
                row = self._by_code.get(str(code))
                return row["name"] if row else ""
            cached = self._name_lookup.get(str(code))
        if cached is not None:
            return cached
        # 全库索引还没建时只查这一行：把整张表读成 dict 比一次全表扫描还贵
        # （amap 的城市库有三千多行，几毫秒的差别在主线程上是能感觉到的）
        rows = self._query("SELECT name FROM citys WHERE city_num = ? LIMIT 1", (str(code),))
        name = _display_name(rows[0][0]) if rows else ""
        with self._lock:
            if len(self._name_lookup) >= _NAME_CACHE_LIMIT:
                self._name_lookup.clear()
            self._name_lookup[str(code)] = name
        return name

    def find_by_name(self, term: str, limit: int = 1) -> List[Dict[str, Any]]:
        """按汉字名模糊匹配（只用汉字名，不碰拼音表）。

        数据源切换时 / 每次刷新都要给已有城市找编号，会在主线程上调用，所以这里
        必须保持毫秒级：全库索引已经建好就直接在内存里过滤，否则退回一次 SQL。
        """
        term = _strip_region(term)
        if not term:
            return []
        key = (term, int(limit))
        with self._lock:
            cached = self._name_cache.get(key)
            rows = self._rows
        if cached is not None:
            return [dict(row) for row in cached]
        if rows is None:
            matched = [
                _row_to_city(name, code, province_id, order)
                for name, code, province_id, order in self._query(
                    "SELECT name, city_num, province_id, _id FROM citys WHERE name LIKE ?"
                    " ORDER BY LENGTH(name), _id LIMIT ?",
                    (f"%{term}%", int(limit)),
                )
            ]
        else:
            matched = [row for row in rows if term in row["name"]]
            matched.sort(key=lambda row: (len(row["name"]), row["order"]))
            matched = [dict(row) for row in matched[:limit]]
        with self._lock:
            if len(self._name_cache) >= _NAME_CACHE_LIMIT:
                self._name_cache.clear()
            self._name_cache[key] = [dict(row) for row in matched]
        return matched

    def popular(self, limit: int = 31) -> List[Dict[str, Any]]:
        """热门城市：拿内置直辖市 / 省会的名字去库里换编号。"""
        rows = self._ensure_rows()
        result: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for name, latitude, longitude in MAJOR_CHINESE_CITIES[:limit]:
            row = self._by_name.get(name)
            if row is None:
                matches = self.find_by_name(name, limit=1)
                row = matches[0] if matches else None
            if row is not None:
                if row["code"] in seen:
                    continue
                seen.add(row["code"])
                result.append(dict(row))
            else:
                # 库里没有这个名字时仍然展示，方便只认坐标的数据源
                result.append({
                    "name": name, "code": "", "pinyin": "", "pinyin_abbr": "",
                    "latitude": latitude, "longitude": longitude,
                })
        return result

    # ------------------------------------------------------------ 重量查询（工作线程）

    def _ensure_rows(self) -> List[Dict[str, Any]]:
        with self._lock:
            if self._rows is not None:
                return self._rows
            rows = _build_index_rows(self._query(
                "SELECT name, city_num, province_id, _id FROM citys ORDER BY province_id, _id"
            ))
            by_name: Dict[str, Dict[str, Any]] = {}
            by_code: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                by_name.setdefault(row["name"], row)
                by_code.setdefault(row["code"], row)
            self._rows = rows
            self._by_name = by_name
            self._by_code = by_code
            return rows

    def searchable(self) -> List[Dict[str, Any]]:
        """带拼音的全量索引；第一次调用要读一次拼音表。

        整个构建都在锁里，所以无论多少个线程同时搜，只有一个人真的在建索引。
        """
        with self._lock:
            if self._searchable is None:
                table = _pinyin_table(self._database)
                searchable: List[Dict[str, Any]] = []
                for row in self._ensure_rows():
                    syllables = table.get(row["name"])
                    if syllables is None:
                        syllables = " ".join(_pinyin_syllables(row["name"]))
                    parts = syllables.split()
                    searchable.append(row | {
                        "pinyin": "".join(parts),
                        "pinyin_abbr": "".join(part[:1] for part in parts),
                        # 音节之间留空格：子串匹配必须落在音节边界上，否则
                        # 「hangzhou」会命中「changzhou」。
                        "pinyin_parts": " " + " ".join(parts),
                        "abbr_parts": " " + " ".join(part[:1] for part in parts),
                    })
                self._searchable = searchable
            return self._searchable

    def warm_up(self) -> None:
        """提前把全库索引 + 拼音表准备好。**只允许在工作线程调用。**"""
        self.searchable()

    def cities(self, province_index: int, limit: int = PROVINCE_CITY_LIMIT) -> List[Dict[str, Any]]:
        """``provinces._id`` 从 1 开始，所以这里用 ``cities.province_id``。

        ``province_index < 0`` 返回热门城市而不是全库几千条——设置页拿到的是
        QJSValue，全量列表既慢又没法看。
        """
        if province_index < 0:
            return self.popular()
        rows = self._ensure_rows()
        return [dict(row) for row in rows if row["province_id"] == province_index][:limit]

    def search(
        self,
        term: str,
        limit: int = SEARCH_LIMIT,
        province_index: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """汉字 / 全拼 / 首字母三种匹配，跨省（可限定省份）。"""
        term = _strip_region(term)
        if not term:
            return []
        needle = term.lower()
        # 只有拼音 / 首字母查询才需要拼音表，纯汉字查询直接用普通索引。
        rows = self.searchable() if _is_pinyin_query(needle) else self._ensure_rows()
        if province_index is not None and province_index >= 0:
            rows = [row for row in rows if row["province_id"] == province_index]

        matched: List[Tuple[Tuple[int, ...], Dict[str, Any]]] = []
        for row in rows:
            rank = _match_rank(row, needle)
            if rank is not None:
                name = row["name"]
                # 同分时短的、且是「市 / 县」本体的排前面（西安 ≺ 西安区）
                matched.append(((rank, len(name), 0 if name[-1:] in ("市", "县") else 1, name), row))
        matched.sort(key=lambda item: item[0])
        return [dict(row) for _, row in matched[:limit]]


_INDEXES: Dict[str, _CityIndex] = {}
_INDEX_LOCK = threading.Lock()


def index_for(database: str) -> _CityIndex:
    path = DATA_DIR / database
    key = str(path)
    with _INDEX_LOCK:
        index = _INDEXES.get(key)
        if index is None:
            index = _CityIndex(path, database)
            _INDEXES[key] = index
    return index


def _strip_region(term: str) -> str:
    return str(term or "").strip().replace("市", "").replace("区", "")


def _is_pinyin_query(needle: str) -> bool:
    """查询里有没有需要靠拼音才能匹配的部分。"""
    return any(character.isascii() and character.isalpha() for character in needle)


def _match_rank(row: Dict[str, Any], needle: str) -> Optional[int]:
    """命中返回排序权重（越小越靠前），不命中返回 ``None``。

    拼音子串一律要求落在音节边界上，否则「changzhou」（常州）会被「hangzhou」
    命中。索引没建拼音时（纯汉字查询）只按名字匹配。
    """
    name = row["name"]
    if name == needle or (name.startswith(needle) and name[len(needle):] in ("市", "县")):
        return 0
    if name.startswith(needle):
        return 1
    if needle in name:
        return 2
    abbreviation = row.get("pinyin_abbr")
    if not abbreviation:
        return None
    if abbreviation.startswith(needle):
        return 3
    if row["pinyin"].startswith(needle):
        return 4
    if f" {needle}" in row["pinyin_parts"]:
        return 5
    if needle in row["abbr_parts"]:
        return 6
    return None


def _row_to_city(name: str, code: str, province_id: int, order: int) -> Dict[str, Any]:
    return {
        "name": _display_name(name),
        "code": str(code),
        "province_id": int(province_id),
        "order": int(order),
        **_coordinates_of(name),
    }


class CityRepository:
    """只读访问城市表，``citys(_id, province_id, name, city_num)``。"""

    def __init__(self, database: str):
        self.database = database
        self._index = index_for(database)

    @property
    def available(self) -> bool:
        return self._index.available

    def provinces(self) -> List[str]:
        if not self._index.available:
            return []
        return self._index.provinces()

    def cities(self, province_index: int, limit: int = PROVINCE_CITY_LIMIT) -> List[Dict[str, str]]:
        """``provinces._id`` 从 1 开始，``citys.province_id`` 从 0 开始。

        ``province_index < 0`` 时返回热门城市。**会建内存索引，只在工作线程调用。**
        """
        if not self._index.available:
            return []
        return self._index.cities(province_index, limit)

    def popular(self) -> List[Dict[str, str]]:
        if not self._index.available:
            return []
        return self._index.popular()

    def warm_up(self) -> None:
        """后台预热全库索引 + 拼音表。**只允许在工作线程调用。**"""
        if not self._index.available:
            return
        self._index.warm_up()

    def search(
        self,
        term: str,
        limit: int = SEARCH_LIMIT,
        province_index: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        """汉字 / 全拼 / 首字母匹配。**会建内存索引，只在工作线程调用。**"""
        if not self._index.available:
            return []
        return self._index.search(term, limit, province_index)

    def search_by_name(self, term: str, limit: int = 1) -> List[Dict[str, str]]:
        """只按汉字名匹配，不建索引，主线程可安全调用。"""
        if not self._index.available:
            return []
        return self._index.find_by_name(term, limit)

    def name_for_code(self, code: str) -> str:
        if not code or not self._index.available:
            return ""
        return self._index.name_for_code(code)
