'''
数据模型与流式追加存储模块
支持 JSONL、CSV 流式写入，解耦采集与持久化，提供断点续传（Checkpoint）机制。
'''
import csv
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, Set, List


@dataclass
class XiaoQuItem:
    '''统一标准化的小区数据项模型'''
    city: str = ""
    district: str = ""
    bizcircle: str = ""
    name: str = ""
    url: str = ""
    price: str = ""
    on_sale_count: str = ""
    build_year: str = ""
    address: str = ""
    address_chain: str = ""
    coords: str = ""
    latitude: str = ""
    longitude: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    crawl_time: str = ""

    def __post_init__(self):
        if not self.crawl_time:
            self.crawl_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        # 拆分经纬度
        if self.coords and (not self.latitude or not self.longitude):
            self._parse_coords()

    def _parse_coords(self):
        if not self.coords:
            return
        parts = self.coords.replace(';', ',').split(',')
        if len(parts) >= 2:
            try:
                # 常见格式为 lng, lat 或 lat, lng
                p1, p2 = parts[0].strip(), parts[1].strip()
                f1, f2 = float(p1), float(p2)
                # 经度通常在 73-136 之间，纬度在 3-54 之间
                if f1 > f2 and f1 > 60:
                    self.longitude, self.latitude = p1, p2
                else:
                    self.latitude, self.longitude = p1, p2
            except ValueError:
                pass

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data

    def to_flat_dict(self) -> Dict[str, Any]:
        '''打平成扁平字典，便于直接写入 CSV'''
        flat = {
            'source': self.source,
            'city': self.city,
            'district': self.district,
            'bizcircle': self.bizcircle,
            'name': self.name,
            'url': self.url,
            'price': self.price,
            'on_sale_count': self.on_sale_count,
            'build_year': self.build_year,
            'address': self.address,
            'address_chain': self.address_chain,
            'coords': self.coords,
            'longitude': self.longitude,
            'latitude': self.latitude,
            'crawl_time': self.crawl_time,
        }
        # 将 details 中的键值打平
        for k, v in self.details.items():
            clean_k = str(k).strip().replace('\n', '')
            clean_v = str(v).strip().replace('\n', '')
            flat[clean_k] = clean_v
        return flat


CSV_STANDARD_FIELDS = [
    'source', 'city', 'district', 'bizcircle', 'name', 'url',
    'price', 'on_sale_count', 'build_year', 'address', 'address_chain',
    'coords', 'longitude', 'latitude', 'crawl_time', 'details_json'
]


class StreamStorage:
    '''流式数据追加存储与断点续传器

    特性：
    1. 线程安全的 JSONL / CSV 追加写入，杜绝高频重写全量 JSON 的 I/O 阻塞。
    2. 基于 URL / 商圈的断点续传检查，支持任务中断后即时恢复。
    3. 自动同步内存与磁盘 Checkpoint 状态。
    '''
    def __init__(
        self,
        source: str,
        city: str,
        output_dir: str = "./data",
        format: str = "jsonl",
        checkpoint_dir: Optional[str] = None
    ):
        self.source = source
        self.city = city
        self.format = format.lower()
        self.city_dir = os.path.join(output_dir, source)
        os.makedirs(self.city_dir, exist_ok=True)

        self.jsonl_path = os.path.join(self.city_dir, f"{city}.jsonl")
        self.csv_path = os.path.join(self.city_dir, f"{city}.csv")

        # 断点文件路径
        if checkpoint_dir is not None:
            self.checkpoint_dir = checkpoint_dir
        else:
            self.checkpoint_dir = os.path.join(output_dir, "checkpoints")
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        self.checkpoint_path = os.path.join(self.checkpoint_dir, f"{source}_{city}_ckpt.json")

        self._lock = threading.Lock()
        self._visited_urls: Set[str] = set()
        self._completed_bizcircles: Set[str] = set()

        # 加载既有断点数据
        self._load_checkpoint()
        self._load_existing_records()

    @staticmethod
    def _normalize_url(url: str) -> str:
        '''规范化 URL（去除空白与尾部斜杠），确保去重判断的一致性'''
        if not url:
            return ""
        return url.strip().rstrip('/')

    def _load_checkpoint(self):
        '''加载持久化的商圈完成进度'''
        if os.path.exists(self.checkpoint_path):
            try:
                with open(self.checkpoint_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._completed_bizcircles = set(data.get('completed_bizcircles', []))
            except Exception:
                self._completed_bizcircles = set()

    def _save_checkpoint(self):
        '''保存当前完成的商圈进度（原子文件替换，避免异常崩溃损坏原断点文件）'''
        data = {
            'source': self.source,
            'city': self.city,
            'completed_bizcircles': sorted(list(self._completed_bizcircles)),
            'updated_time': time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }
        tmp_path = f"{self.checkpoint_path}.tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.checkpoint_path)
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _load_existing_records(self):
        '''从已存在的文件加载去重 URL'''
        if os.path.exists(self.jsonl_path):
            try:
                with open(self.jsonl_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                item_dict = json.loads(line)
                                if 'url' in item_dict and item_dict['url']:
                                    self._visited_urls.add(self._normalize_url(item_dict['url']))
                            except Exception:
                                pass
            except Exception:
                pass

        if os.path.exists(self.csv_path):
            try:
                with open(self.csv_path, 'r', encoding='utf-8', errors='ignore') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        url = row.get('url')
                        if url:
                            self._visited_urls.add(self._normalize_url(url))
            except Exception:
                pass

    def is_url_visited(self, url: str) -> bool:
        '''检查某个小区详情 URL 是否已抓取入库'''
        clean = self._normalize_url(url)
        if not clean:
            return False
        with self._lock:
            return clean in self._visited_urls

    def is_bizcircle_completed(self, district: str, bizcircle: str) -> bool:
        '''检查某个商圈/板块是否已完整抓取'''
        key = f"{district}:{bizcircle}"
        with self._lock:
            return key in self._completed_bizcircles

    def mark_bizcircle_completed(self, district: str, bizcircle: str):
        '''标记商圈/板块完成并持久化 checkpoint'''
        key = f"{district}:{bizcircle}"
        with self._lock:
            self._completed_bizcircles.add(key)
            self._save_checkpoint()

    def append_item(self, item: XiaoQuItem):
        '''追加单条数据（线程安全）'''
        self.append_items([item])

    def append_items(self, items: List[XiaoQuItem]):
        '''批量追加多条数据（线程安全，降低磁盘 I/O 打开关闭开销）'''
        if not items:
            return
        with self._lock:
            for item in items:
                clean_url = self._normalize_url(item.url)
                if clean_url:
                    self._visited_urls.add(clean_url)

            # 1. 写入 JSONL
            if self.format in ('jsonl', 'both', 'all'):
                with open(self.jsonl_path, 'a', encoding='utf-8') as f:
                    for item in items:
                        f.write(json.dumps(item.to_dict(), ensure_ascii=False) + '\n')

            # 2. 写入 CSV
            if self.format in ('csv', 'both', 'all'):
                file_exists = os.path.exists(self.csv_path) and os.path.getsize(self.csv_path) > 0
                with open(self.csv_path, 'a', encoding='utf-8-sig', newline='') as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(CSV_STANDARD_FIELDS)
                    for item in items:
                        row = [
                            item.source,
                            item.city,
                            item.district,
                            item.bizcircle,
                            item.name,
                            item.url,
                            item.price,
                            item.on_sale_count,
                            item.build_year,
                            item.address,
                            item.address_chain,
                            item.coords,
                            item.longitude,
                            item.latitude,
                            item.crawl_time,
                            json.dumps(item.details, ensure_ascii=False) if item.details else ''
                        ]
                        writer.writerow(row)

    def get_visited_count(self) -> int:
        with self._lock:
            return len(self._visited_urls)
