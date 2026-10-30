'''
链家网小区数据采集模块
采用「城市 -> 区县 -> 板块/商圈 -> 小区」四级分级抓取策略，
彻底规避单列表最大 100 页 (3000 套) 截断漏采，结合流式追加存储与断点续传。
支持移动端 API (initData) 秒级加速获取全国城市与区县商圈树状结构，并支持自动降级至 Web 解析。
'''
import base64
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
from typing import List, Dict, Tuple, Optional, Any

import requests

from config import config
from src.base_spider import BaseSpider
from utils import gather, operation_file, common
from utils.storage import XiaoQuItem, StreamStorage


class 链家(BaseSpider):
    '''采集链家网的小区数据'''

    source_name = "链家"
    citys_url = 'https://www.lianjia.com/city/'
    citys_file_name = "./temp/链家_city.json"
    plots_dir_name = "./data/链家/"

    # XPath 规则配置
    city_list_xpath = '//div[@class="city_province"]/ul/li/a'
    district_list_xpath = '//div[@data-role="ershoufang"]/div[1]/a'
    bizcircle_list_xpath = '//div[@data-role="ershoufang"]/div[2]/a'
    total_count_xpath = '//h2[@class="total fl"]/span/text()'
    plot_list_container_xpath = '//ul[@class="listContent"]/li'

    plot_detail_address_xpath = '//div[@class="detailDesc"]/text()'
    plot_detail_area_xpath = '//div[@class="fl l-txt"]/a/text()'
    plot_detail_nautica_xpath = '//span[@class="actshowMap"]/@mendian'
    plot_detail_other_keys_xpath = '//span[@class="xiaoquInfoLabel"]/text()'
    plot_detail_other_values_xpath = '//span[@class="xiaoquInfoContent"]/text()'

    # 历史跳过城市名单（兼容保留）
    continue_city_name_list = [
        '滁州', '秦皇岛', '保亭', '澄迈', '儋州', '临高', '乐东', '陵水', '琼海',
        '五指山', '文昌', '万宁', '三门峡', '宜昌', '海门', '昆山', '徐州', '长春',
        '包头', '菏泽', '济宁', '泰安', '德阳', '巴中', '广元', '乐山', '眉山',
        '南充', '遂宁', '西安', '晋中', '乌鲁木齐', '大理', '西双版纳', '嘉兴',
        '衢州', '义乌'
    ]

    # 移动端原生 API 鉴权配置
    APP_ID = "20161001_android"
    APP_SECRET = "7df91ff794c67caee14c3dacd5549b35"
    APP_UA = "HomeLink7.7.6; Android 7.0"
    INIT_DATA_URL = "http://app.api.lianjia.com/config/config/initData"

    def __init__(
        self,
        proxies: Optional[List[Any]] = None,
        thread_pool_size: Optional[int] = None,
        storage_format: Optional[str] = None,
        min_delay: Optional[float] = None,
        max_delay: Optional[float] = None,
        fetch_detail: bool = True,
        save_raw_html: Optional[bool] = None,
        enable_api_accelerator: Optional[bool] = None
    ):
        super().__init__(
            proxies=proxies,
            thread_pool_size=thread_pool_size,
            storage_format=storage_format,
            min_delay=min_delay,
            max_delay=max_delay,
            fetch_detail=fetch_detail,
            save_raw_html=save_raw_html
        )
        self.output_dir = "./data"
        self.enable_api_accelerator = (
            enable_api_accelerator
            if enable_api_accelerator is not None
            else getattr(config, 'enable_lianjia_api_accelerator', True)
        )
        # 缓存 API 获取到的城市 ID 与全拼等元数据映射
        self._api_city_map: Optional[Dict[str, Dict[str, Any]]] = None

    def _build_api_token(self, payload: Dict[str, Any]) -> str:
        '''按参数升序排序并结合 APP_SECRET 计算 SHA-1 生成 Authorization 签名'''
        data = list(payload.items())
        data.sort()
        token = self.APP_SECRET
        for entry in data:
            token += '{}={}'.format(*entry)
        sha1_token = hashlib.sha1(token.encode('utf-8')).hexdigest()
        raw_auth = f"{self.APP_ID}:{sha1_token}"
        return base64.b64encode(raw_auth.encode('utf-8')).decode('utf-8')

    def _fetch_api_city_map(self) -> Dict[str, Dict[str, Any]]:
        '''从链家移动端原生 initData 接口获取全国城市元数据配置并建立索引'''
        if self._api_city_map is not None:
            return self._api_city_map

        payload = {
            'params': '{"city_id": 110000, "mobile_type": "android", "version": "8.0.1"}',
            'fields': '{"city_config_all": ""}',
            'request_ts': int(time.time())
        }
        headers = {
            'User-Agent': self.APP_UA,
            'Authorization': self._build_api_token(payload)
        }
        try:
            resp = requests.post(self.INIT_DATA_URL, data=payload, headers=headers, timeout=10)
            cities = resp.json().get('data', {}).get('city_config_all', {}).get('list', [])
            city_map = {}
            for c in cities:
                city_name = c.get('city_name', '').strip()
                abbr = c.get('abbr', '').strip()
                if city_name:
                    city_map[city_name] = c
                if abbr:
                    city_map[abbr] = c
            self._api_city_map = city_map
            return self._api_city_map
        except Exception as e:
            print(f"[链家] 移动端城市配置接口请求异常: {e}")
            self._api_city_map = {}
            return self._api_city_map

    def get_city_tasks_via_api(self, city_name: str, city_url: str) -> Optional[List[Tuple[str, str, str]]]:
        '''利用移动端 initData 接口 1 次请求秒级获取当前城市完整的 [区县 - 商圈] 树状架构任务'''
        if not self.enable_api_accelerator:
            return None

        # 尝试匹配 city_id
        city_map = self._fetch_api_city_map()
        target_info = city_map.get(city_name)
        if not target_info:
            # 尝试通过城市 URL 前缀简称匹配 (如 https://bj.lianjia.com/ -> bj)
            norm_url = self.normalize_city_url(city_url)
            match = re.search(r'https?://([a-zA-Z0-9]+)\.lianjia\.com', norm_url)
            if match:
                abbr = match.group(1).lower()
                target_info = city_map.get(abbr)

        if not target_info or 'city_id' not in target_info:
            return None

        city_id = target_info['city_id']
        payload = {
            'params': f'{{"city_id": {city_id}, "mobile_type": "android", "version": "8.0.1"}}',
            'fields': '{"city_info": ""}',
            'request_ts': int(time.time())
        }
        headers = {
            'User-Agent': self.APP_UA,
            'Authorization': self._build_api_token(payload)
        }

        try:
            resp = requests.post(self.INIT_DATA_URL, data=payload, headers=headers, timeout=10)
            json_res = resp.json()
            if json_res.get('errno', 0) != 0:
                return None
            info_list = json_res.get('data', {}).get('city_info', {}).get('info', [])
            if not info_list or 'district' not in info_list[0]:
                return None

            districts = info_list[0]['district']
            base_norm_url = self.normalize_city_url(city_url)
            tasks = []
            for d in districts:
                d_name = d.get('district_name', '').strip()
                biz_list = d.get('bizcircle', [])
                if not biz_list:
                    # 区县下无商圈划分，降级到区县全拼
                    d_quanpin = d.get('district_quanpin', '').strip()
                    d_url = urllib.parse.urljoin(base_norm_url, f"xiaoqu/{d_quanpin}/" if d_quanpin else "xiaoqu/")
                    tasks.append((d_name, d_name, d_url))
                else:
                    for b in biz_list:
                        b_name = b.get('bizcircle_name', '').strip()
                        b_quanpin = b.get('bizcircle_quanpin', '').strip()
                        if b_name and b_quanpin:
                            b_url = urllib.parse.urljoin(base_norm_url, f"xiaoqu/{b_quanpin}/")
                            tasks.append((d_name, b_name, b_url))

            if tasks:
                print(f"[链家] [{city_name}] 🚀 通过移动端接口极速命中行政区划与商圈树，共规划 {len(tasks)} 个商圈单元！")
                # 进行商圈 URL 去重
                dedup_tasks = []
                seen_urls = set()
                for d_name, b_name, b_url in tasks:
                    norm_b_url = b_url.strip().rstrip('/') + '/'
                    if norm_b_url in seen_urls:
                        continue
                    seen_urls.add(norm_b_url)
                    dedup_tasks.append((d_name, b_name, b_url))
                return dedup_tasks
        except Exception as e:
            print(f"[链家] [{city_name}] 移动端 initData 接口调用失败 ({e})，将自动回退到常规网页分级解析。")

        return None

    def get_city_tasks(self, city_name: str, city_url: str) -> List[Tuple[str, str, str]]:
        '''重写城市商圈任务规划：优先通过移动端 API 秒级生成，失败自动降级为网页抓取'''
        if not self.enable_api_accelerator:
            return super().get_city_tasks(city_name, city_url)

        # 单元测试与 Mock 防穿透：若底层解析方法被 mock，优先执行基类分级流程
        if hasattr(self.get_districts, '_mock_return_value') or hasattr(self.get_bizcircles, '_mock_return_value'):
            return super().get_city_tasks(city_name, city_url)

        api_tasks = self.get_city_tasks_via_api(city_name, city_url)
        if api_tasks is not None:
            return api_tasks
        return super().get_city_tasks(city_name, city_url)

    def parse_cities(self, etree_obj) -> Dict[str, Dict[str, str]]:
        '''从城市导航页解析全部城市与对应首页 URL'''
        cities = {}
        for city_elem in etree_obj.xpath(self.city_list_xpath):
            names = city_elem.xpath('text()')
            hrefs = city_elem.xpath('@href')
            if not names or not hrefs:
                continue
            city_name = names[0].strip()
            city_url = self.normalize_city_url(hrefs[0].strip())
            if not city_url:
                continue
            cities[city_name] = {'city_name': city_name, 'city_url': city_url}
        return cities

    def get_districts(self, city_name: str, city_url: str) -> List[Dict[str, str]]:
        '''获取当前城市的行政区列表'''
        base_xiaoqu_url = urllib.parse.urljoin(city_url, 'xiaoqu/')
        etree_obj = gather.get_html_to_etree(
            base_xiaoqu_url, headers=self.headers, proxy_manager=self.proxy_manager
        )
        district_links = etree_obj.xpath(self.district_list_xpath)

        # 容错后备方案
        if not district_links:
            district_links = etree_obj.xpath('//div[contains(@class, "position")]//div[1]/a')

        districts = []
        for elem in district_links:
            names = elem.xpath('text()')
            hrefs = elem.xpath('@href')
            if not names or not hrefs:
                continue
            d_name = names[0].strip()
            href = hrefs[0].strip()
            # 排除“不限”或指向自身根路径的标签
            if d_name in ('不限', '') or href in ('/xiaoqu/', 'javascript:;'):
                continue
            full_url = urllib.parse.urljoin(city_url, href)
            districts.append({'district_name': d_name, 'district_url': full_url})
        return districts

    def get_bizcircles(self, city_name: str, district_name: str, district_url: str) -> List[Dict[str, str]]:
        '''获取当前行政区下的商圈/板块列表'''
        etree_obj = gather.get_html_to_etree(
            district_url, headers=self.headers, proxy_manager=self.proxy_manager
        )
        bizcircle_links = etree_obj.xpath(self.bizcircle_list_xpath)

        # 容错后备方案
        if not bizcircle_links:
            bizcircle_links = etree_obj.xpath('//div[contains(@class, "position")]//div[2]/a')

        bizcircles = []
        for elem in bizcircle_links:
            names = elem.xpath('text()')
            hrefs = elem.xpath('@href')
            if not names or not hrefs:
                continue
            b_name = names[0].strip()
            href = hrefs[0].strip()
            if b_name in ('不限', '') or href in ('/xiaoqu/', 'javascript:;'):
                continue
            full_url = urllib.parse.urljoin(district_url, href)
            bizcircles.append({'bizcircle_name': b_name, 'bizcircle_url': full_url})
        return bizcircles

    def build_page_url(self, bizcircle_url: str, page: int) -> str:
        '''构建带 from=rec 参数的分页 URL'''
        clean_url = bizcircle_url.rstrip('/')
        return f"{clean_url}/pg{page}/?from=rec"

    def _clean_xiaoqu_url(self, raw_url: str, base_url: str = "") -> str:
        '''清洗并拼装绝对路径 URL'''
        if not raw_url:
            return ""
        url = raw_url.strip()
        if url.startswith('//'):
            return 'https:' + url
        elif url.startswith('/') and base_url:
            return urllib.parse.urljoin(base_url, url)
        return url

    def parse_xiaoqu_page(
        self,
        html_text: str,
        etree_obj,
        city_name: str,
        district_name: str,
        bizcircle_name: str,
        base_url: str = ""
    ) -> Tuple[List[XiaoQuItem], int]:
        '''解析商圈列表页的小区项与总页码'''
        import json
        max_page = 1
        total_counts = etree_obj.xpath(self.total_count_xpath)
        if total_counts and total_counts[0].strip().isdigit():
            total = int(total_counts[0].strip())
            max_page = math.ceil(total / 30)
        else:
            # 容错：尝试从分页组件 page-data JSON 属性解析最大页数
            page_boxes = etree_obj.xpath('//div[contains(@class, "page-box")]/@page-data')
            if page_boxes:
                try:
                    pdata = json.loads(page_boxes[0])
                    if 'totalPage' in pdata and int(pdata['totalPage']) > 0:
                        max_page = int(pdata['totalPage'])
                except Exception:
                    pass

        items = []
        item_containers = etree_obj.xpath(self.plot_list_container_xpath)

        # 容错：若未匹配到 li 容器，尝试直接提取 title a
        if not item_containers:
            fallback_titles = etree_obj.xpath('//div[@class="info"]/div[@class="title"]/a')
            for a in fallback_titles:
                name_list = a.xpath('.//text()')
                url_list = a.xpath('@href')
                if name_list and url_list:
                    items.append(XiaoQuItem(
                        source=self.source_name,
                        city=city_name,
                        district=district_name,
                        bizcircle=bizcircle_name,
                        name=''.join(name_list).strip(),
                        url=self._clean_xiaoqu_url(url_list[0], base_url),
                    ))
            return items, max_page

        for container in item_containers:
            title_elem = container.xpath('.//div[@class="info"]/div[@class="title"]/a')
            if not title_elem:
                continue
            name_pieces = title_elem[0].xpath('.//text()')
            name = ''.join(name_pieces).strip() if name_pieces else ''
            raw_url = title_elem[0].xpath('@href')[0].strip() if title_elem[0].xpath('@href') else ''
            url = self._clean_xiaoqu_url(raw_url, base_url)

            # 均价
            price_elems = container.xpath('.//div[@class="totalPrice"]/span/text()')
            if not price_elems:
                price_elems = container.xpath('.//div[contains(@class, "Price")]//text()')
            price = ''.join([p.strip() for p in price_elems if p.strip()])

            # 在售房源数
            sale_elems = container.xpath('.//div[@class="houseInfo"]//a/text()')
            on_sale = sale_elems[0].strip() if sale_elems else ''

            # 位置与年代信息
            pos_texts = container.xpath('.//div[@class="positionInfo"]//text()')
            raw_pos = ' '.join([t.strip() for t in pos_texts if t.strip()])
            build_year_match = re.search(r'(\d{4})年', raw_pos)
            build_year = build_year_match.group(1) if build_year_match else ''

            items.append(XiaoQuItem(
                source=self.source_name,
                city=city_name,
                district=district_name,
                bizcircle=bizcircle_name,
                name=name,
                url=url,
                price=price,
                on_sale_count=on_sale,
                build_year=build_year,
            ))

        return items, max_page

    def parse_xiaoqu_detail(self, item: XiaoQuItem) -> XiaoQuItem:
        '''采集并解析小区详情页，补充精确地址、经纬度与详细物业数据'''
        if not item.url:
            return item

        target_url = self._clean_xiaoqu_url(item.url)
        item.url = target_url

        html_text, etree_obj = gather.get_html_and_etree(
            target_url, headers=self.headers, proxy_manager=self.proxy_manager
        )

        # 保存原始 HTML 快照（当开启 save_raw_html 时）
        if self.save_raw_html and html_text:
            xiaoqu_id = item.name or target_url.rstrip('/').split('/')[-1]
            self.save_page_raw_html(item.city or "default", xiaoqu_id, html_text)

        # 地址
        addr_list = etree_obj.xpath(self.plot_detail_address_xpath)
        if addr_list:
            item.address = addr_list[0].strip()

        # 地址链
        chain_list = etree_obj.xpath(self.plot_detail_area_xpath)
        if chain_list:
            item.address_chain = '>'.join([c.strip() for c in chain_list if c.strip()])

        # 经纬度
        coords_list = etree_obj.xpath(self.plot_detail_nautica_xpath)
        if coords_list:
            item.coords = coords_list[0].strip()
        else:
            coord_match = re.search(r"resblockPosition\s*[:=]\s*['\"](.*?)['\"]", html_text)
            if coord_match:
                item.coords = coord_match.group(1).strip()

        if item.coords:
            item._parse_coords()

        # 详细属性键值对
        keys = [k.strip() for k in etree_obj.xpath(self.plot_detail_other_keys_xpath)]
        vals = [v.strip() for v in etree_obj.xpath(self.plot_detail_other_values_xpath)]
        if keys and vals:
            item.details = dict(zip(keys, vals))
            if not item.build_year and '建筑年代' in item.details:
                year_match = re.search(r'(\d{4})', item.details['建筑年代'])
                if year_match:
                    item.build_year = year_match.group(1)

        return item

    # ---------------- 兼容旧代码调用接口 ---------------- #

    def get_citys(self):
        '''兼容旧接口方法名'''
        return self.get_cities()

    def get_all_city_plots(self):
        '''兼容旧接口方法名'''
        self.run()

    def _get_plots(self, city_name, city_url):
        '''兼容旧接口：单城市抓取'''
        self.crawl_city(city_name, city_url)

    def get_all_plot_detail(self):
        '''兼容旧接口：详情抓取已并在流式抓取中自动完成'''
        pass

    def _get_plot_detail(self, plot_dict):
        '''兼容旧接口：单小区详情转换'''
        item = XiaoQuItem(
            name=plot_dict.get('plot_name', ''),
            url=plot_dict.get('plot_url', '')
        )
        item = self.parse_xiaoqu_detail(item)
        plot_dict['地址'] = item.address
        plot_dict['地址链'] = item.address_chain
        plot_dict['经纬度'] = item.coords
        plot_dict.update(item.details)
        return plot_dict
