'''
贝壳网小区数据采集模块
采用「城市 -> 区县 -> 板块/商圈 -> 小区」四级分级抓取策略，
彻底规避单列表最大 100 页 (3000 套) 截断漏采，结合流式追加存储与断点续传。
'''
import math
import os
import re
import urllib.parse
from typing import List, Dict, Tuple, Optional, Any

from config import config
from src.base_spider import BaseSpider
from utils import gather, operation_file, common
from utils.storage import XiaoQuItem, StreamStorage


class 贝壳(BaseSpider):
    '''贝壳网小区信息采集'''

    source_name = "贝壳"
    citys_url = "https://www.ke.com/city/"
    citys_file_name = './temp/贝壳_city.json'
    plots_dir_name = './data/贝壳/'

    # XPath 规则配置
    citys_xpath = (
        '//div[@data-action="Uicode=选择城市_国内"]//div[@class="city_list"]//ul/li/a'
        ' | //div[@class="city_list_section"]//ul[@class="city_list_ul"]//li/a'
    )
    district_list_xpath = '//div[@data-role="ershoufang"]/div[1]/a'
    bizcircle_list_xpath = '//div[@data-role="ershoufang"]/div[2]/a'
    total_count_xpath = '//h2[@class="total fl"]/span/text()'
    plots_error_xpath = '//div[@class="sec-list-nav clearfix"]'

    plot_list_container_xpath = '//ul[@class="listContent"]/li'
    maidian_detail_xpath = '//a[@class="maidian-detail"]'

    地址_xpath = '//div[@class="sub"]/text() | //div[@class="detailDesc"]/text()'
    地址链_xpath = '//div[@class="fl l-txt"]/a/text()'
    详情_keys_xpath = '//span[@class="xiaoquInfoLabel"]/text()'
    详情_values_xpath = '//span[@class="xiaoquInfoContent"]/text()'
    经纬度_re = r"resblockPosition\s*[:=]\s*['\"](.*?)['\"]"

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

    def parse_cities(self, etree_obj) -> Dict[str, Dict[str, str]]:
        '''解析贝壳城市列表'''
        cities_dict = {}
        for city_elem in etree_obj.xpath(self.citys_xpath):
            names = city_elem.xpath('text()')
            hrefs = city_elem.xpath('@href')
            if not names or not hrefs:
                continue
            city_name = names[0].strip()
            city_url = self.normalize_city_url(hrefs[0].strip())
            if not city_url:
                continue
            cities_dict[city_name] = {'city_name': city_name, 'city_url': city_url}
        return cities_dict

    def is_city_supported(self, city_name: str, city_url: str) -> bool:
        '''检查贝壳该城市是否开通二手房小区业务'''
        if not city_url or '.fang.' in city_url:
            return False
        base_xiaoqu_url = urllib.parse.urljoin(city_url, 'xiaoqu/')
        etree_obj = gather.get_html_to_etree(
            base_xiaoqu_url, headers=self.headers, proxy_manager=self.proxy_manager
        )
        if etree_obj.xpath(self.plots_error_xpath):
            return False
        return True

    def get_districts(self, city_name: str, city_url: str) -> List[Dict[str, str]]:
        '''获取贝壳当前城市的行政区列表'''
        if not city_url or '.fang.' in city_url:
            return []

        base_xiaoqu_url = urllib.parse.urljoin(city_url, 'xiaoqu/')
        etree_obj = gather.get_html_to_etree(
            base_xiaoqu_url, headers=self.headers, proxy_manager=self.proxy_manager
        )

        if etree_obj.xpath(self.plots_error_xpath):
            return []

        district_links = etree_obj.xpath(self.district_list_xpath)
        if not district_links:
            district_links = etree_obj.xpath('//a[@class=" CLICKDATA"]')
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
            if d_name in ('不限', '') or href in ('/xiaoqu/', 'javascript:;'):
                continue
            full_url = urllib.parse.urljoin(city_url, href)
            districts.append({'district_name': d_name, 'district_url': full_url})
        return districts

    def get_bizcircles(self, city_name: str, district_name: str, district_url: str) -> List[Dict[str, str]]:
        '''获取贝壳当前行政区下的商圈/板块列表'''
        etree_obj = gather.get_html_to_etree(
            district_url, headers=self.headers, proxy_manager=self.proxy_manager
        )
        bizcircle_links = etree_obj.xpath(self.bizcircle_list_xpath)

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
        '''构建贝壳分页 URL'''
        clean_url = bizcircle_url.rstrip('/')
        return f"{clean_url}/pg{page}/"

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
        '''解析贝壳列表页的小区列表与总页码'''
        import json
        max_page = 1
        total_counts = etree_obj.xpath(self.total_count_xpath)
        if total_counts and total_counts[0].strip().isdigit():
            total = int(total_counts[0].strip())
            max_page = math.ceil(total / 30)
        else:
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

        if item_containers:
            for container in item_containers:
                title_elem = (
                    container.xpath('.//div[@class="info"]/div[@class="title"]/a')
                    or container.xpath('.//a[@class="maidian-detail"]')
                )
                if not title_elem:
                    continue
                name_pieces = title_elem[0].xpath('.//text()')
                name = ''.join(name_pieces).strip() if name_pieces else ''
                raw_url = title_elem[0].xpath('@href')[0].strip() if title_elem[0].xpath('@href') else ''
                url = self._clean_xiaoqu_url(raw_url, base_url)

                price_elems = container.xpath('.//div[@class="totalPrice"]/span/text()')
                if not price_elems:
                    price_elems = container.xpath('.//div[contains(@class, "Price")]//text()')
                price = ''.join([p.strip() for p in price_elems if p.strip()])

                sale_elems = container.xpath('.//div[@class="houseInfo"]//a/text()')
                on_sale = sale_elems[0].strip() if sale_elems else ''

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
        else:
            # 兼容旧版的 maidian-detail 选择器
            maidian_links = etree_obj.xpath(self.maidian_detail_xpath)
            for a in maidian_links:
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

    def parse_xiaoqu_detail(self, item: XiaoQuItem) -> XiaoQuItem:
        '''解析贝壳小区详情页'''
        if not item.url:
            return item

        target_url = self._clean_xiaoqu_url(item.url)
        item.url = target_url

        html_text, etree_obj = gather.get_html_and_etree(
            target_url, headers=self.headers, proxy_manager=self.proxy_manager
        )

        # 地址
        addr_list = etree_obj.xpath(self.地址_xpath)
        if addr_list:
            item.address = addr_list[0].strip().replace('\n', '').replace(' ', '')

        # 地址链
        chain_list = etree_obj.xpath(self.地址链_xpath)
        if chain_list:
            item.address_chain = '>'.join([c.strip() for c in chain_list if c.strip()])

        # 经纬度
        coord_match = re.search(self.经纬度_re, html_text)
        if coord_match:
            item.coords = coord_match.group(1).strip()
        else:
            mendian_list = etree_obj.xpath('//span[@class="actshowMap"]/@mendian')
            if mendian_list:
                item.coords = mendian_list[0].strip()

        if item.coords:
            item._parse_coords()

        # 属性字典
        keys = [k.strip() for k in etree_obj.xpath(self.详情_keys_xpath)]
        vals = [v.strip().replace('\n', '').replace(' ', '') for v in etree_obj.xpath(self.详情_values_xpath)]
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

    def get_plots(self, city_name, city_url):
        '''兼容旧接口：单城市抓取'''
        self.crawl_city(city_name, city_url)

    def get_plots_detail(self, file_name):
        '''兼容旧接口：详情抓取已在流式流程中完成'''
        pass

    def _get_plots_detail(self, plot_dict):
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
