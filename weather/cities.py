"""城市数据库查询（data/*.db 来自 Class Widgets）。"""

import sqlite3
from typing import Dict, List, Optional, Tuple

from pypinyin import Style, lazy_pinyin

from . import DATA_DIR


def _display_name(raw: str) -> str:
    """``北京.海淀`` -> ``北京 海淀``。"""
    return raw.replace(".", " ")


def _pinyin(text: str) -> Tuple[str, str]:
    """返回 (全拼, 首字母缩写)，均小写无空格。例：'北京' -> ('beijing', 'bj')。"""
    clean = text.replace(" ", "").replace(".", "")
    full = "".join(lazy_pinyin(clean))
    abbr = "".join(lazy_pinyin(clean, style=Style.FIRST_LETTER))
    return full, abbr


def _city_dict(name: str, code: str, province_id: int | None = None) -> Dict[str, str]:
    display = _display_name(name)
    full, abbr = _pinyin(name)
    d: Dict[str, str] = {"name": display, "code": str(code), "pinyin": full, "pinyin_abbr": abbr}
    if province_id is not None:
        d["province_id"] = province_id
    return d


class CityRepository:
    """只读访问城市表，``citys(_id, province_id, name, city_num)``。"""

    def __init__(self, database: str):
        self.database = database
        self._path = DATA_DIR / database

    def _connect(self) -> Optional[sqlite3.Connection]:
        if not self._path.exists():
            return None
        return sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)

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
