"""Архив .zip для импорта каталога с фото: таблица (.xlsx/.csv) и картинки в одном файле.

Фото к строке берутся из колонки «Фото» (имена файлов через запятую) или, если колонки нет,
по имени файла: A100.jpg, A100_2.jpg, A100-3.png — к артикулу A100.
"""
import re
import zipfile
from pathlib import PurePosixPath

import photos

TABLE_EXT = (".xlsx", ".xlsm", ".csv")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
MAX_ENTRIES = 5000
MAX_TABLE_BYTES = 20 * 1024 * 1024


class ArchiveError(ValueError):
    pass


def _key(name):
    return PurePosixPath(name).name.strip().lower()


class PhotoArchive:
    def __init__(self, path):
        try:
            self.zf = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError):
            raise ArchiveError("Не удалось открыть .zip — архив повреждён.")
        entries = [i for i in self.zf.infolist()
                   if not i.is_dir() and not i.filename.startswith("__MACOSX/") and not _key(i.filename).startswith(".")]
        if len(entries) > MAX_ENTRIES:
            self.close()
            raise ArchiveError(f"В архиве больше {MAX_ENTRIES} файлов.")
        tables = [i for i in entries if _key(i.filename).endswith(TABLE_EXT)]
        if not tables:
            self.close()
            raise ArchiveError("В архиве нет таблицы .xlsx или .csv с каталогом.")
        self.table_info = sorted(tables, key=lambda i: (i.filename.count("/"), i.filename))[0]
        self.images = {}
        for i in entries:
            if _key(i.filename).endswith(IMAGE_EXT):
                self.images.setdefault(_key(i.filename), i)

    def table(self):
        info = self.table_info
        if info.file_size > MAX_TABLE_BYTES:
            raise ArchiveError("Таблица в архиве слишком большая.")
        return self.zf.read(info), _key(info.filename)

    def find(self, name):
        return self.images.get(_key(name))

    def for_article(self, article):
        """Картинки, названные по артикулу: A100.jpg, A100_2.jpg, A100-2.png, «A100 (2).jpg»."""
        a = article.strip().lower()
        pattern = re.compile(rf"^{re.escape(a)}(?:[ _\-]\(?(\d+)\)?)?\.[a-z]+$")
        found = []
        for key, info in self.images.items():
            m = pattern.match(key)
            if m:
                found.append((int(m.group(1) or 0), key, info))
        return [info for _, _, info in sorted(found)]

    def read(self, info):
        """Байты картинки; None, если она больше допустимого (не распаковываем «бомбы»)."""
        if info.file_size > photos.MAX_BYTES:
            return None
        with self.zf.open(info) as f:
            data = f.read(photos.MAX_BYTES + 1)
        return data if len(data) <= photos.MAX_BYTES else None

    def close(self):
        self.zf.close()
