'''
爬虫抽象基类 BaseSpider
提供标准化的四级分级采集流程 (城市 -> 区县 -> 商圈/板块 -> 小区)、
细粒度并发控制 (ThreadPoolExecutor)、流式追加存储集成 (StreamStorage) 与智能断点续传。
'''
import math
import os
import random
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional, Any

from config import config
from utils import gather, operation_file, ua
from utils.proxies import ProxyManager, global_proxy_manager
from utils.storage import XiaoQuItem, StreamStorage


class BaseSpider:
    '''通用小区爬虫基类'''

    source_name = "Base"
    citys_url = ""
    citys_file_name = "./temp/base_city.json"
    output_dir = "./data"

    # 默认跳过的城市列表（兼容原业务配置）
    continue_city_name_list = []

    def __init__(
        self,
        proxies: Optional[List[Any]] = None,
        thread_pool_size: Optional[int] = None,
        storage_format: Optional[str] = None,
        min_delay: Optional[float] = None,
        max_delay: Optional[float] = None,
        fetch_detail: bool = True,
        save_raw_html: Optional[bool] = None
    ):
        self.proxies = proxies or getattr(config, 'proxy_pool', [])
        if proxies:
            self.proxy_manager = ProxyManager(proxies)
        elif getattr(config, 'enable_proxy', False):
            self.proxy_manager = global_proxy_manager
        else:
            self.proxy_manager = None

        self.thread_pool_size = thread_pool_size or getattr(config, 'thread_pool_size', 3)
        self.storage_format = storage_format or getattr(config, 'storage_format', 'both')
        self.min_delay = min_delay if min_delay is not None else getattr(config, 'min_delay', 1.0)
        self.max_delay = max_delay if max_delay is not None else getattr(config, 'max_delay', 2.5)
        self.fetch_detail = fetch_detail
        self.save_raw_html = save_raw_html if save_raw_html is not None else getattr(config, 'save_raw_html', False)
        self.headers = config.universal_headers.copy()

    def random_sleep(self, min_s: Optional[float] = None, max_s: Optional[float] = None):
        '''休眠随机时长，减缓反爬特征'''
        if self.max_delay <= 0:
            return
        low = min_s if min_s is not None else self.min_delay
        high = max_s if max_s is not None else self.max_delay
        delay = random.uniform(low, high)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def normalize_city_url(url: str) -> str:
        '''规范化城市 URL: 处理协议相对路径 //，提取纯净的 {scheme}://{netloc}/，去除分页模板与查询参数。非法或非 http(s) URL 返回空字符串。'''
        if not url or not isinstance(url, str):
            return ""
        clean = url.strip()
        if not clean:
            return ""

        # 过滤锚点、查询参数单独起始、以冒号或单斜杠开头的相对路径
        if clean.startswith(("#", "?", ":")):
            return ""
        if clean.startswith("/") and not clean.startswith("//"):
            return ""

        if clean.startswith("//"):
            clean = "https:" + clean
        elif not clean.startswith(("http://", "https://")):
            # 检查是否包含非 http/https 的自定义伪协议 (如 javascript:, mailto:, ftp:, about:)
            first_part = clean.split("/")[0]
            if ":" in first_part:
                colon_parts = first_part.split(":", 1)
                # 仅当冒号后是纯数字时视为端口号 (如 localhost:8080)，否则为伪协议或非法格式
                if not (colon_parts[1].isdigit() and (colon_parts[0] == "localhost" or "." in colon_parts[0])):
                    return ""
            clean = "https://" + clean.lstrip("/")

        try:
            parsed = urllib.parse.urlsplit(clean)
        except Exception:
            return ""

        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return ""

        try:
            hostname = parsed.hostname
        except ValueError:
            return ""

        if not hostname:
            return ""

        # 域名必须合法：包含点号（如 bj.lianjia.com, 127.0.0.1）或是 localhost
        if "." not in hostname and hostname != "localhost":
            return ""

        return f"{scheme}://{parsed.netloc}/"

    def is_city_supported(self, city_name: str, city_url: str) -> bool:
        '''检查该城市是否支持抓取小区业务，子类可重写覆盖'''
        return True

    def get_cities(self) -> Dict[str, Dict[str, str]]:
        '''获取城市列表字典并持久化缓存。子类可重写提取逻辑。'''
        if os.path.exists(self.citys_file_name):
            data = operation_file.read_json_file(self.citys_file_name)
            if data:
                updated = False
                for c_name, c_info in list(data.items()):
                    if isinstance(c_info, dict) and 'city_url' in c_info:
                        norm = self.normalize_city_url(c_info['city_url'])
                        if norm != c_info['city_url']:
                            c_info['city_url'] = norm
                            updated = True
                    elif isinstance(c_info, str):
                        norm = self.normalize_city_url(c_info)
                        data[c_name] = {'city_name': c_name, 'city_url': norm}
                        updated = True
                if updated:
                    operation_file.write_json_file(self.citys_file_name, data)
                return data

        print(f"[{self.source_name}] 正在获取城市列表: {self.citys_url}")
        etree = gather.get_html_to_etree(self.citys_url, headers=self.headers, proxy_manager=self.proxy_manager)
        cities = self.parse_cities(etree)
        if cities:
            for c_name, c_info in list(cities.items()):
                if isinstance(c_info, dict) and 'city_url' in c_info:
                    c_info['city_url'] = self.normalize_city_url(c_info['city_url'])
                elif isinstance(c_info, str):
                    cities[c_name] = {'city_name': c_name, 'city_url': self.normalize_city_url(c_info)}
            operation_file.write_json_file(self.citys_file_name, cities)
        return cities

    def parse_cities(self, etree_obj) -> Dict[str, Dict[str, str]]:
        '''解析城市页面 etree，需子类实现或使用通用规则'''
        raise NotImplementedError("子类必须实现 parse_cities 或配置通用解析规则")

    def get_districts(self, city_name: str, city_url: str) -> List[Dict[str, str]]:
        '''获取指定城市的区县列表。返回 [{'district_name': ..., 'district_url': ...}]'''
        raise NotImplementedError("子类必须实现 get_districts")

    def get_bizcircles(self, city_name: str, district_name: str, district_url: str) -> List[Dict[str, str]]:
        '''获取指定区县下的商圈/板块列表。返回 [{'bizcircle_name': ..., 'bizcircle_url': ...}]'''
        raise NotImplementedError("子类必须实现 get_bizcircles")

    def save_page_raw_html(self, city_name: str, identifier: str, html_content: str):
        '''将原始 HTML 快照存入本地磁盘 (例如 data/{source}/raw_html/{city}/{id}.html)'''
        if not self.save_raw_html or not html_content:
            return
        try:
            safe_id = re.sub(r'[^\w\-]', '_', str(identifier))
            dump_dir = os.path.join(self.output_dir, self.source_name, "raw_html", city_name)
            os.makedirs(dump_dir, exist_ok=True)
            dump_file = os.path.join(dump_dir, f"{safe_id}.html")
            with open(dump_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
        except Exception as e:
            print(f"[{self.source_name}] 保存原始 HTML 快照失败 ({identifier}): {e}")

    def get_city_tasks(self, city_name: str, city_url: str) -> List[Tuple[str, str, str]]:
        '''规划单城市的商圈/板块抓取任务列表: [(district_name, bizcircle_name, bizcircle_url), ...]'''
        districts = self.get_districts(city_name, city_url)
        tasks = []
        if districts:
            print(f"[{self.source_name}] [{city_name}] 发现 {len(districts)} 个区县。")
            for d in districts:
                d_name, d_url = d['district_name'], d['district_url']
                self.random_sleep(0.5, 1.0)
                bizcircles = self.get_bizcircles(city_name, d_name, d_url)
                if bizcircles:
                    for b in bizcircles:
                        tasks.append((d_name, b['bizcircle_name'], b['bizcircle_url']))
                else:
                    # 降级容错：区县下无商圈划分时，以区县作为最小抓取单元
                    tasks.append((d_name, d_name, d_url))
        else:
            # 降级容错：城市下无区县划分时，以城市自身作为最小抓取单元
            clean_city_url = city_url if city_url.endswith('/') else city_url + '/'
            base_xiaoqu_url = urllib.parse.urljoin(clean_city_url, 'xiaoqu/')
            tasks.append((city_name, city_name, base_xiaoqu_url))

        # 跨区商圈任务去重 (按规范化商圈 URL 过滤重复项)
        dedup_tasks = []
        seen_urls = set()
        for d_name, b_name, b_url in tasks:
            norm_b_url = b_url.strip().rstrip('/') + '/'
            if norm_b_url in seen_urls:
                continue
            seen_urls.add(norm_b_url)
            dedup_tasks.append((d_name, b_name, b_url))
        return dedup_tasks

    def build_page_url(self, bizcircle_url: str, page: int) -> str:
        '''根据商圈 URL 与页码构建分页请求地址'''
        clean_url = bizcircle_url.rstrip('/')
        return f"{clean_url}/pg{page}/"

    def parse_xiaoqu_page(
        self,
        html_text: str,
        etree_obj,
        city_name: str,
        district_name: str,
        bizcircle_name: str,
        base_url: str = ""
    ) -> Tuple[List[XiaoQuItem], int]:
        '''解析单个列表页的小区列表与该商圈总页数'''
        raise NotImplementedError("子类必须实现 parse_xiaoqu_page")

    def parse_xiaoqu_detail(self, item: XiaoQuItem) -> XiaoQuItem:
        '''请求并解析小区详情页，补全地址、经纬度与属性键值对'''
        raise NotImplementedError("子类必须实现 parse_xiaoqu_detail")

    def crawl_bizcircle(
        self,
        city_name: str,
        district_name: str,
        bizcircle_name: str,
        bizcircle_url: str,
        storage: StreamStorage
    ) -> int:
        '''抓取单一商圈下的全部小区数据（流式入库 + 断点续传，异常不丢数据且不盲目标记完成）'''
        if storage.is_bizcircle_completed(district_name, bizcircle_name):
            print(f"[{self.source_name}] 商圈 [{district_name}-{bizcircle_name}] 已在断点记录中完成，跳过。")
            return 0

        print(f"[{self.source_name}] 开始采集商圈 [{city_name} - {district_name} - {bizcircle_name}]: {bizcircle_url}")
        page = 1
        max_page = 1
        total_items_count = 0
        crawl_success = True

        while page <= max_page and page <= 100:
            page_url = self.build_page_url(bizcircle_url, page)
            self.random_sleep()
            html_text, tree = gather.get_html_and_etree(
                page_url, headers=self.headers, proxy_manager=self.proxy_manager
            )

            # 网络请求彻底失败或被反爬拦截重定向，中止本次商圈抓取并不标记为已完成
            if not html_text:
                print(f"[{self.source_name}] ⚠️ 商圈 [{district_name} - {bizcircle_name}] 第 {page} 页获取失败或被拦截，商圈未完成，保留下次重试。")
                crawl_success = False
                break

            items, parsed_max_page = self.parse_xiaoqu_page(
                html_text, tree, city_name, district_name, bizcircle_name, base_url=page_url
            )

            # 首页获取最大页码
            if page == 1:
                max_page = min(parsed_max_page, 100) if parsed_max_page > 0 else 1

            if not items:
                # 页面无数据：若在第 1 页说明此商圈确实无房源，正常结束；若在后续页说明提前截断，正常结束
                break

            items_to_save = []
            for item in items:
                # 检查 URL 是否已在持久化数据中
                if storage.is_url_visited(item.url):
                    continue

                if self.fetch_detail and item.url:
                    try:
                        self.random_sleep(0.5, 1.2)
                        item = self.parse_xiaoqu_detail(item)
                    except Exception as e:
                        print(f"[{self.source_name}] 解析小区详情异常 ({item.name} - {item.url}): {e}")

                items_to_save.append(item)
                total_items_count += 1

            # 批量流式写入存储
            if items_to_save:
                storage.append_items(items_to_save)

            page += 1

        # 仅在抓取完整无致命错误时标记商圈采集完成
        if crawl_success:
            storage.mark_bizcircle_completed(district_name, bizcircle_name)
            print(f"[{self.source_name}] 完成商圈 [{district_name} - {bizcircle_name}]，新增入库 {total_items_count} 个小区。")
        else:
            print(f"[{self.source_name}] 商圈 [{district_name} - {bizcircle_name}] 未完全采集，断点记录已保留。")
        return total_items_count

    def crawl_city(self, city_name: str, city_url: str):
        '''执行单城市的完整分级抓取任务'''
        city_url = self.normalize_city_url(city_url)
        if not city_url:
            print(f"[{self.source_name}] [{city_name}] 城市 URL 非法或为空，跳过采集。")
            return

        print(f"\n{'='*20} [{self.source_name}] 启动城市采集: {city_name} ({city_url}) {'='*20}")

        # 检查城市支持度
        if not self.is_city_supported(city_name, city_url):
            print(f"[{self.source_name}] [{city_name}] 暂无二手房小区业务或不支持采集，跳过。")
            return

        storage = StreamStorage(
            source=self.source_name,
            city=city_name,
            output_dir=self.output_dir,
            format=self.storage_format,
            checkpoint_dir=getattr(self, 'checkpoint_dir', None) or os.path.join(self.output_dir, "checkpoints")
        )

        # 1. 获取该城市下所有商圈抓取任务
        tasks = self.get_city_tasks(city_name, city_url)

        print(f"[{self.source_name}] [{city_name}] 共规划 {len(tasks)} 个商圈抓取任务单元。")

        # 过滤已完成的商圈任务
        pending_tasks = [
            t for t in tasks if not storage.is_bizcircle_completed(t[0], t[1])
        ]
        print(f"[{self.source_name}] [{city_name}] 待执行任务: {len(pending_tasks)} / {len(tasks)}")

        if not pending_tasks:
            print(f"[{self.source_name}] [{city_name}] 所有商圈均已完成，跳过该城市。")
            return

        # 3. 细粒度并发调度 (基于商圈级别的 ThreadPoolExecutor)
        if self.thread_pool_size > 1:
            print(f"[{self.source_name}] 启用线程池并发抓取，并发线程数: {self.thread_pool_size}")
            with ThreadPoolExecutor(max_workers=self.thread_pool_size) as executor:
                future_to_task = {
                    executor.submit(
                        self.crawl_bizcircle, city_name, d_name, b_name, b_url, storage
                    ): (d_name, b_name)
                    for d_name, b_name, b_url in pending_tasks
                }
                for future in as_completed(future_to_task):
                    d_name, b_name = future_to_task[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[{self.source_name}] 任务失败 [{d_name}-{b_name}]: {e}")
        else:
            # 单线程顺序执行
            for d_name, b_name, b_url in pending_tasks:
                try:
                    self.crawl_bizcircle(city_name, d_name, b_name, b_url, storage)
                except Exception as e:
                    print(f"[{self.source_name}] 任务失败 [{d_name}-{b_name}]: {e}")

        print(f"[{self.source_name}] [{city_name}] 采集任务结束，当前已入库总数: {storage.get_visited_count()}\n")

    def run(self):
        '''爬虫执行主入口'''
        cities = self.get_cities()
        if not cities:
            print(f"[{self.source_name}] 无法获取城市列表，请检查网络或配置。")
            return

        for city_name, city_info in cities.items():
            if city_name in self.continue_city_name_list:
                print(f"[{self.source_name}] 城市在跳过名单中: {city_name}")
                continue
            if isinstance(city_info, dict):
                city_url = city_info.get('city_url', '')
            else:
                city_url = str(city_info)
            city_url = self.normalize_city_url(city_url)
            if not city_url:
                continue
            self.crawl_city(city_name, city_url)
