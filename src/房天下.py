'''
房天下 (fang.com) 小区数据采集模块
目标站点: fang.com
采用「城市 -> 区县 -> 商圈/板块 -> 小区」四级分级抓取策略，
结合 Schema.org 结构化数据解析、流式追加存储与断点续传。
'''
import json
import math
import os
import re
import urllib.parse
from typing import List, Dict, Tuple, Optional, Any

from config import config
from src.base_spider import BaseSpider
from utils import gather, operation_file
from utils.storage import XiaoQuItem, StreamStorage


class 房天下(BaseSpider):
    '''采集房天下 (fang.com) 的小区数据'''

    source_name = "房天下"
    citys_url = "https://www.fang.com/SoufunFamily.htm"
    citys_file_name = "./temp/房天下_city.json"
    plots_dir_name = "./data/房天下/"

    def __init__(
        self,
        proxies: Optional[List[Any]] = None,
        thread_pool_size: Optional[int] = None,
        storage_format: Optional[str] = None,
        min_delay: Optional[float] = None,
        max_delay: Optional[float] = None,
        fetch_detail: bool = True
    ):
        super().__init__(
            proxies=proxies,
            thread_pool_size=thread_pool_size,
            storage_format=storage_format,
            min_delay=min_delay,
            max_delay=max_delay,
            fetch_detail=fetch_detail
        )
        self.output_dir = "./data"

    def get_housing_url(self, city_url: str) -> str:
        '''将城市主页 URL 转换为对应的二手房/小区频道 URL'''
        clean_url = self.normalize_city_url(city_url)
        parsed = urllib.parse.urlparse(clean_url)
        host = parsed.netloc.lower()
        parts = host.split('.')
        prefix = parts[0] if parts else ''

        if prefix in ('bj', 'www', 'www1', ''):
            return "https://esf.fang.com/housing/"
        return f"https://{prefix}.esf.fang.com/housing/"

    def parse_cities(self, etree_obj) -> Dict[str, Dict[str, str]]:
        '''从房天下全国城市导航页解析全部城市与对应首页 URL'''
        cities = {}
        links = etree_obj.xpath('//table[@id="senfe"]//a') or etree_obj.xpath('//a[contains(@href, ".fang.com")]')
        for a in links:
            names = a.xpath('text()')
            hrefs = a.xpath('@href')
            if not names or not hrefs:
                continue
            city_name = names[0].strip()
            raw_url = hrefs[0].strip()
            clean_url = self.normalize_city_url(raw_url)
            if not clean_url:
                continue

            # 排除非城市或导航功能标签
            if not city_name or len(city_name) > 6:
                continue
            if any(k in city_name for k in ['关于', '合作', '加盟', '招聘', '网站', 'APP', '手机', '房天下', '论坛', '集团', '全国']):
                continue
            if '.fang.com' not in clean_url:
                continue

            cities[city_name] = {'city_name': city_name, 'city_url': clean_url}
        return cities

    def get_districts(self, city_name: str, city_url: str) -> List[Dict[str, str]]:
        '''获取当前城市的行政区列表'''
        housing_url = self.get_housing_url(city_url)
        etree_obj = gather.get_html_to_etree(
            housing_url, headers=self.headers, proxy_manager=self.proxy_manager
        )
        links = etree_obj.xpath('//a[contains(@href, "/housing/")]')
        districts = []
        seen = set()

        for a in links:
            names = a.xpath('text()')
            hrefs = a.xpath('@href')
            if not names or not hrefs:
                continue
            d_name = names[0].strip()
            href = hrefs[0].strip()

            if not d_name or d_name in ('不限', '全部', '找小区', '写字楼', '商铺', '首页', '地铁') or len(d_name) > 10:
                continue
            if d_name in seen or 'javascript' in href.lower():
                continue

            # 房天下行政区 URL 特征: /housing/\d+__
            if not re.search(r'/housing/\d+__', href):
                continue

            seen.add(d_name)
            full_url = urllib.parse.urljoin(housing_url, href)
            districts.append({'district_name': d_name, 'district_url': full_url})

        return districts

    def get_bizcircles(self, city_name: str, district_name: str, district_url: str) -> List[Dict[str, str]]:
        '''获取当前行政区下的商圈/板块列表'''
        etree_obj = gather.get_html_to_etree(
            district_url, headers=self.headers, proxy_manager=self.proxy_manager
        )
        d_id_match = re.search(r'/housing/(\d+)__', district_url)
        d_id = d_id_match.group(1) if d_id_match else ''
        bizcircles = []
        seen = set()

        # 匹配下辖商圈: /housing/{district_id}_\d+_
        xpath_query = f'//a[contains(@href, "/housing/{d_id}_")]' if d_id else '//a[contains(@href, "/housing/")]'
        for a in etree_obj.xpath(xpath_query):
            names = a.xpath('text()')
            hrefs = a.xpath('@href')
            if not names or not hrefs:
                continue
            b_name = names[0].strip()
            href = hrefs[0].strip()

            if not b_name or b_name in (district_name, '不限', '全部') or b_name in seen or len(b_name) > 15:
                continue
            if 'javascript' in href.lower():
                continue

            seen.add(b_name)
            full_url = urllib.parse.urljoin(district_url, href)
            bizcircles.append({'bizcircle_name': b_name, 'bizcircle_url': full_url})

        return bizcircles

    def build_page_url(self, bizcircle_url: str, page: int) -> str:
        '''根据商圈 URL 与页码构建房天下分页地址'''
        clean_url = bizcircle_url.rstrip('/')
        # 1. 商圈级分页: 替换 _0_0_0_0_1_0_0_0 或 _1_0_0_0 为目标页码
        sub_pattern = r'_\d+_0_0_0_0_\d+_0_0_0'
        if re.search(sub_pattern, clean_url):
            return re.sub(r'(_\d+_0_0_0_0_)\d+(_0_0_0)', rf'\g<1>{page}\g<2>', clean_url) + '/'

        # 2. 行政区级分页: __0_3_0_0_\d+_0_0_0
        dist_pattern = r'__0_3_0_0_\d+_0_0_0'
        if re.search(dist_pattern, clean_url):
            return re.sub(r'__0_3_0_0_\d+_0_0_0', f'__0_3_0_0_{page}_0_0_0', clean_url) + '/'

        # 3. 基础兜底
        if clean_url.endswith('/housing'):
            return f"{clean_url}/__0_3_0_0_{page}_0_0_0/"
        return f"{clean_url}/i3{page}/"

    def parse_xiaoqu_page(
        self,
        html_text: str,
        etree_obj,
        city_name: str,
        district_name: str,
        bizcircle_name: str,
        base_url: str = ""
    ) -> Tuple[List[XiaoQuItem], int]:
        '''解析房天下列表页中的小区项目与总页数'''
        items: List[XiaoQuItem] = []
        cards = etree_obj.xpath('//dl[contains(@class, "plotList")]')

        for card in cards:
            tit_elems = card.xpath('.//a[contains(@class, "plotTit")]')
            if not tit_elems:
                continue
            name = tit_elems[0].xpath('string(.)').strip()
            raw_url = tit_elems[0].xpath('@href')[0].strip() if tit_elems[0].xpath('@href') else ''
            if not name or not raw_url:
                continue

            full_url = urllib.parse.urljoin(base_url, raw_url)
            fang_type = ''.join(card.xpath('.//span[contains(@class, "plotFangType")]/text()')).strip()

            # 解析地址与商圈文本
            p_text = ''.join(card.xpath('.//dd/p[2]//text()')).strip()
            address = p_text

            # 售租数量与建筑年代
            sell_count = ''.join(card.xpath('.//li[contains(text(), "在售")]//text()')).strip()
            rent_count = ''.join(card.xpath('.//li[contains(text(), "在租")]//text()')).strip()
            built_year = ''.join(card.xpath('.//li[contains(text(), "建成")]//text()')).strip()

            item = XiaoQuItem(
                name=name,
                city=city_name,
                district=district_name,
                bizcircle=bizcircle_name,
                url=full_url,
                address=address,
                on_sale_count=sell_count,
                build_year=built_year,
                details={
                    "物业类型": fang_type,
                    "在售房源": sell_count,
                    "在租房源": rent_count,
                    "建筑年代": built_year,
                }
            )
            items.append(item)

        # 解析最大页数
        max_page = 1
        fanye_elems = etree_obj.xpath('//div[contains(@class, "fanye")]')
        if fanye_elems:
            spans_text = ''.join(fanye_elems[0].xpath('.//text()'))
            m_span = re.search(r'共(\d+)页', spans_text)
            if m_span:
                max_page = int(m_span.group(1))
            else:
                last_a = fanye_elems[0].xpath('.//a[contains(text(), "末页")]/@href')
                if last_a:
                    m_href = re.search(r'_0_0_(\d+)_0_0_0', last_a[0])
                    if m_href:
                        max_page = int(m_href.group(1))

        return items, min(max_page, 100)

    def parse_xiaoqu_detail(self, item: XiaoQuItem) -> XiaoQuItem:
        '''请求并解析房天下小区详情页，优先提取 Schema.org 结构化 JSON-LD'''
        html_text, tree = gather.get_html_and_etree(
            item.url, headers=self.headers, proxy_manager=self.proxy_manager
        )
        if not html_text:
            return item

        # 1. 优先解析 Schema.org JSON-LD
        ld_scripts = tree.xpath('//script[@type="application/ld+json"]/text()')
        for script_text in ld_scripts:
            try:
                data = json.loads(script_text.strip())
                if isinstance(data, dict) and data.get('@type') in ('Place', 'ApartmentComplex', 'Residence'):
                    if data.get('name'):
                        item.name = data['name']

                    # 地址提取
                    addr_data = data.get('address')
                    if isinstance(addr_data, dict):
                        street = addr_data.get('streetAddress', '')
                        region = addr_data.get('addressRegion', '')
                        locality = addr_data.get('addressLocality', '')
                        full_addr = f"{locality}{region}{street}".strip() or street
                        if full_addr:
                            item.address = full_addr

                    # 经纬度
                    geo = data.get('geo')
                    if isinstance(geo, dict):
                        lat = geo.get('latitude')
                        lng = geo.get('longitude')
                        if lat and lng:
                            item.latitude = str(lat)
                            item.longitude = str(lng)

                    # 均价
                    offers = data.get('offers')
                    if isinstance(offers, dict):
                        price = offers.get('lowPrice') or offers.get('highPrice')
                        if price:
                            item.price = f"{price}元/㎡"

                    # 附加属性
                    props = data.get('additionalProperty', [])
                    if isinstance(props, list):
                        for p in props:
                            if isinstance(p, dict) and p.get('name') and p.get('value'):
                                item.details[str(p['name'])] = str(p['value'])

                    return item
            except Exception:
                pass

        # 2. 备用兜底：XPath 解析
        price_elems = tree.xpath('//span[contains(@class, "num_price")]/text()') or tree.xpath('//span[contains(@class, "price")]/text()')
        if price_elems:
            item.price = price_elems[0].strip()

        addr_elems = tree.xpath('//span[contains(@class, "info_add")]/text()')
        if addr_elems:
            item.address = addr_elems[0].strip()

        return item
