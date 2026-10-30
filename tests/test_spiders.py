'''
爬虫框架及链家、贝壳分级抓取单元测试
'''
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from lxml import etree
from src.base_spider import BaseSpider
from src.链家 import 链家
from src.贝壳 import 贝壳
from src.房天下 import 房天下
from utils.storage import XiaoQuItem, StreamStorage
from utils import operation_file


SAMPLE_LIANJIA_CITIES_HTML = '''
<div class="city_province">
    <ul>
        <li><a href="//bj.lianjia.com/">北京</a></li>
        <li><a href="https://sh.lianjia.com/">上海</a></li>
    </ul>
</div>
'''

SAMPLE_LIANJIA_DISTRICTS_HTML = '''
<div data-role="ershoufang">
    <div>
        <a href="/xiaoqu/">不限</a>
        <a href="/xiaoqu/dongcheng/">东城</a>
        <a href="/xiaoqu/chaoyang/">朝阳</a>
    </div>
    <div>
        <a href="/xiaoqu/dongdan/">东单</a>
    </div>
</div>
'''

SAMPLE_LIANJIA_BIZCIRCLES_HTML = '''
<div data-role="ershoufang">
    <div>
        <a href="/xiaoqu/dongcheng/">东城</a>
    </div>
    <div>
        <a href="/xiaoqu/dongcheng/">不限</a>
        <a href="/xiaoqu/dongdan/">东单</a>
        <a href="/xiaoqu/dongzhimen/">东直门</a>
    </div>
</div>
'''

SAMPLE_LIANJIA_XIAOQU_PAGE_HTML = '''
<h2 class="total fl">共找到<span>65</span>个小区</h2>
<ul class="listContent">
    <li class="clear xiaoquListItem">
        <div class="info">
            <div class="title">
                <a href="https://bj.lianjia.com/xiaoqu/1001.html">东方银座</a>
            </div>
            <div class="positionInfo">
                <a class="district" href="/xiaoqu/dongcheng/">东城</a>
                <a class="bizcircle" href="/xiaoqu/dongzhimen/">东直门</a>
                / 2004年建成
            </div>
            <div class="houseInfo">
                <a href="https://bj.lianjia.com/ershoufang/c1001/">9套在售</a>
            </div>
        </div>
        <div class="xiaoquListItemRight">
            <div class="totalPrice"><span>85000</span>元/m²</div>
        </div>
    </li>
    <li class="clear xiaoquListItem">
        <div class="info">
            <div class="title">
                <a href="https://bj.lianjia.com/xiaoqu/1002.html">幸福家园</a>
            </div>
            <div class="positionInfo">
                / 2008年建成
            </div>
            <div class="houseInfo">
                <a href="https://bj.lianjia.com/ershoufang/c1002/">3套在售</a>
            </div>
        </div>
        <div class="xiaoquListItemRight">
            <div class="totalPrice"><span>72000</span>元/m²</div>
        </div>
    </li>
</ul>
'''

SAMPLE_LIANJIA_DETAIL_HTML = '''
<div class="detailDesc">北京市东城区东直门外大街1号</div>
<div class="fl l-txt">
    <a href="/xiaoqu/">北京</a>
    <a href="/xiaoqu/dongcheng/">东城</a>
    <a href="/xiaoqu/dongzhimen/">东直门</a>
</div>
<span class="actshowMap" mendian="116.440263,39.944324"></span>
<div>
    <span class="xiaoquInfoLabel">建筑年代</span><span class="xiaoquInfoContent">2004年</span>
    <span class="xiaoquInfoLabel">物业费用</span><span class="xiaoquInfoContent">3.5元/平米/月</span>
    <span class="xiaoquInfoLabel">物业公司</span><span class="xiaoquInfoContent">优质物业</span>
</div>
'''

SAMPLE_BEIKE_CITIES_HTML = '''
<div data-action="Uicode=选择城市_国内">
    <div class="city_list">
        <ul>
            <li><a href="//bj.ke.com/">北京</a></li>
            <li><a href="https://gz.ke.com/">广州</a></li>
        </ul>
    </div>
</div>
'''

SAMPLE_BEIKE_DETAIL_HTML = '''
<div class="sub">北京市海淀区中关村南大街5号</div>
<div class="fl l-txt">
    <a href="/xiaoqu/">北京</a>
    <a href="/xiaoqu/haidian/">海淀</a>
    <a href="/xiaoqu/zhongguancun/">中关村</a>
</div>
<script>
    var resblockPosition = '116.321,39.954';
</script>
<span class="xiaoquInfoLabel">绿化率</span><span class="xiaoquInfoContent">35%</span>
<span class="xiaoquInfoLabel">容积率</span><span class="xiaoquInfoContent">2.1</span>
'''


class TestSpiders(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_lianjia_parse_cities(self):
        spider = 链家()
        tree = etree.HTML(SAMPLE_LIANJIA_CITIES_HTML)
        cities = spider.parse_cities(tree)
        self.assertIn("北京", cities)
        self.assertEqual(cities["北京"]["city_url"], "https://bj.lianjia.com/")
        self.assertIn("上海", cities)
        self.assertEqual(cities["上海"]["city_url"], "https://sh.lianjia.com/")

    @patch('utils.gather.get_html_to_etree')
    def test_lianjia_get_districts_and_bizcircles(self, mock_etree):
        spider = 链家()

        # 测试区县解析
        mock_etree.return_value = etree.HTML(SAMPLE_LIANJIA_DISTRICTS_HTML)
        districts = spider.get_districts("北京", "https://bj.lianjia.com/")
        self.assertEqual(len(districts), 2)
        self.assertEqual(districts[0]['district_name'], "东城")
        self.assertEqual(districts[0]['district_url'], "https://bj.lianjia.com/xiaoqu/dongcheng/")

        # 测试商圈解析
        mock_etree.return_value = etree.HTML(SAMPLE_LIANJIA_BIZCIRCLES_HTML)
        bizcircles = spider.get_bizcircles("北京", "东城", "https://bj.lianjia.com/xiaoqu/dongcheng/")
        self.assertEqual(len(bizcircles), 2)
        self.assertEqual(bizcircles[0]['bizcircle_name'], "东单")
        self.assertEqual(bizcircles[1]['bizcircle_name'], "东直门")

    def test_lianjia_parse_xiaoqu_page(self):
        spider = 链家()
        tree = etree.HTML(SAMPLE_LIANJIA_XIAOQU_PAGE_HTML)
        items, max_page = spider.parse_xiaoqu_page(
            SAMPLE_LIANJIA_XIAOQU_PAGE_HTML, tree, "北京", "东城", "东直门"
        )
        self.assertEqual(max_page, 3)  # ceil(65 / 30) = 3
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].name, "东方银座")
        self.assertEqual(items[0].price, "85000")
        self.assertEqual(items[0].build_year, "2004")
        self.assertEqual(items[0].on_sale_count, "9套在售")
        self.assertEqual(items[1].name, "幸福家园")
        self.assertEqual(items[1].build_year, "2008")

    @patch('utils.gather.get_html_and_etree')
    def test_lianjia_parse_xiaoqu_detail(self, mock_get):
        spider = 链家()
        mock_get.return_value = (SAMPLE_LIANJIA_DETAIL_HTML, etree.HTML(SAMPLE_LIANJIA_DETAIL_HTML))

        item = XiaoQuItem(
            name="东方银座",
            url="https://bj.lianjia.com/xiaoqu/1001.html"
        )
        enriched = spider.parse_xiaoqu_detail(item)
        self.assertEqual(enriched.address, "北京市东城区东直门外大街1号")
        self.assertEqual(enriched.address_chain, "北京>东城>东直门")
        self.assertEqual(enriched.longitude, "116.440263")
        self.assertEqual(enriched.latitude, "39.944324")
        self.assertEqual(enriched.details.get("物业费用"), "3.5元/平米/月")
        self.assertEqual(enriched.details.get("物业公司"), "优质物业")

    def test_beike_parse_cities(self):
        spider = 贝壳()
        tree = etree.HTML(SAMPLE_BEIKE_CITIES_HTML)
        cities = spider.parse_cities(tree)
        self.assertIn("北京", cities)
        self.assertEqual(cities["北京"]["city_url"], "https://bj.ke.com/")
        self.assertIn("广州", cities)

    @patch('utils.gather.get_html_and_etree')
    def test_beike_parse_xiaoqu_detail(self, mock_get):
        spider = 贝壳()
        mock_get.return_value = (SAMPLE_BEIKE_DETAIL_HTML, etree.HTML(SAMPLE_BEIKE_DETAIL_HTML))

        item = XiaoQuItem(
            name="中关村小区",
            url="https://bj.ke.com/xiaoqu/2001.html"
        )
        enriched = spider.parse_xiaoqu_detail(item)
        self.assertEqual(enriched.address, "北京市海淀区中关村南大街5号")
        self.assertEqual(enriched.address_chain, "北京>海淀>中关村")
        self.assertEqual(enriched.longitude, "116.321")
        self.assertEqual(enriched.latitude, "39.954")
        self.assertEqual(enriched.details.get("绿化率"), "35%")
        self.assertEqual(enriched.details.get("容积率"), "2.1")

    @patch('utils.gather.get_html_to_etree')
    @patch('utils.gather.get_html_and_etree')
    def test_hierarchy_crawl_flow(self, mock_and_etree, mock_to_etree):
        '''端到端测试四级抓取流程、并发调度与断点持久化'''
        spider = 链家(
            thread_pool_size=2,
            storage_format="both",
            min_delay=0.0,
            max_delay=0.0,
            fetch_detail=True,
            enable_api_accelerator=False
        )
        spider.output_dir = self.test_dir

        # Mock 区县和商圈
        def fake_to_etree(url, **kw):
            if 'xiaoqu/dongcheng/' in url:
                return etree.HTML(SAMPLE_LIANJIA_BIZCIRCLES_HTML)
            else:
                return etree.HTML(SAMPLE_LIANJIA_DISTRICTS_HTML)

        mock_to_etree.side_effect = fake_to_etree

        # Mock 列表页和详情页
        def fake_and_etree(url, **kw):
            if 'pg' in url:
                # 第一页返回数据，第二页空数据模拟结束
                if 'pg1' in url:
                    return SAMPLE_LIANJIA_XIAOQU_PAGE_HTML, etree.HTML(SAMPLE_LIANJIA_XIAOQU_PAGE_HTML)
                else:
                    return "<html></html>", etree.HTML("<html></html>")
            else:
                # 详情页
                return SAMPLE_LIANJIA_DETAIL_HTML, etree.HTML(SAMPLE_LIANJIA_DETAIL_HTML)

        mock_and_etree.side_effect = fake_and_etree

        # 执行城市抓取
        spider.crawl_city("北京", "https://bj.lianjia.com/")

        # 验证输出文件生成
        jsonl_path = os.path.join(self.test_dir, "链家", "北京.jsonl")
        csv_path = os.path.join(self.test_dir, "链家", "北京.csv")
        self.assertTrue(os.path.exists(jsonl_path))
        self.assertTrue(os.path.exists(csv_path))

        records = operation_file.read_jsonl_file(jsonl_path)
        self.assertGreaterEqual(len(records), 2)
        # 验证详情是否成功补齐
        self.assertTrue(records[0]['address'].startswith("北京市"))
        self.assertTrue(records[0]['longitude'] != "")

        # 验证 checkpoint 记录
        storage = StreamStorage("链家", "北京", output_dir=self.test_dir)
        self.assertTrue(storage.is_bizcircle_completed("东城", "东单"))
        self.assertTrue(storage.is_bizcircle_completed("东城", "东直门"))

    def test_fangtianxia_housing_url(self):
        '''验证房天下基础属性与小区列表URL拼接'''
        spider = 房天下()
        self.assertEqual(spider.source_name, "房天下")
        self.assertEqual(spider.get_housing_url("https://bj.fang.com/"), "https://esf.fang.com/housing/")
        self.assertEqual(spider.get_housing_url("https://cd.fang.com/"), "https://cd.esf.fang.com/housing/")

    def test_fangtianxia_parse_cities(self):
        '''验证房天下全国城市导航解析'''
        html = '''
        <table id="senfe">
            <tr>
                <td><a href="//bj.fang.com/">北京</a></td>
                <td><a href="https://cd.fang.com/">成都</a></td>
                <td><a href="https://sh.fang.com/">上海</a></td>
            </tr>
        </table>
        '''
        spider = 房天下()
        cities = spider.parse_cities(etree.HTML(html))
        self.assertIn("北京", cities)
        self.assertIn("成都", cities)
        self.assertEqual(cities["北京"]["city_url"], "https://bj.fang.com/")

    @patch('utils.gather.get_html_to_etree')
    def test_fangtianxia_get_districts_and_bizcircles(self, mock_to_etree):
        '''验证房天下行政区与商圈提取'''
        districts_html = '''
        <div class="screen">
            <a href="/housing/">不限</a>
            <a href="/housing/129__0_3_0_0_1_0_0_0/">青羊</a>
            <a href="/housing/130__0_3_0_0_1_0_0_0/">锦江</a>
        </div>
        '''
        bizcircles_html = '''
        <div class="screen">
            <a href="/housing/129__0_3_0_0_1_0_0_0/">不限</a>
            <a href="/housing/129_832_0_0_0_0_1_0_0_0/">八宝街</a>
            <a href="/housing/129_5056_0_0_0_0_1_0_0_0/">贝森</a>
        </div>
        '''
        mock_to_etree.side_effect = [
            etree.HTML(districts_html),
            etree.HTML(bizcircles_html)
        ]
        spider = 房天下()
        districts = spider.get_districts("成都", "https://cd.fang.com/")
        self.assertEqual(len(districts), 2)
        self.assertEqual(districts[0]["district_name"], "青羊")
        self.assertEqual(districts[0]["district_url"], "https://cd.esf.fang.com/housing/129__0_3_0_0_1_0_0_0/")

        bizcircles = spider.get_bizcircles("成都", "青羊", districts[0]["district_url"])
        self.assertEqual(len(bizcircles), 2)
        self.assertEqual(bizcircles[0]["bizcircle_name"], "八宝街")
        self.assertEqual(bizcircles[0]["bizcircle_url"], "https://cd.esf.fang.com/housing/129_832_0_0_0_0_1_0_0_0/")

        # 验证分页 URL 构建
        page2_url = spider.build_page_url(bizcircles[0]["bizcircle_url"], 2)
        self.assertEqual(page2_url, "https://cd.esf.fang.com/housing/129_832_0_0_0_0_2_0_0_0/")

    @patch('utils.gather.get_html_and_etree')
    def test_fangtianxia_parse_page_and_detail(self, mock_and_etree):
        '''验证房天下列表页解析与详情页 LD+JSON 结构化解析'''
        page_html = '''
        <html>
        <body>
            <dl class="plotListwrap clearfix plotList">
                <dd>
                    <p><a href="/loupan/1001.htm" class="plotTit">优品道</a><span class="plotFangType">住宅</span></p>
                    <p>青羊-贝森 青羊大道99号</p>
                </dd>
            </dl>
            <div class="fanye gray6">
                <span>共12页</span>
            </div>
        </body>
        </html>
        '''
        spider = 房天下()
        items, max_page = spider.parse_xiaoqu_page(
            page_html,
            etree.HTML(page_html),
            "成都",
            "青羊",
            "贝森",
            base_url="https://cd.esf.fang.com/housing/129_5056_0_0_0_0_1_0_0_0/"
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "优品道")
        self.assertEqual(items[0].url, "https://cd.esf.fang.com/loupan/1001.htm")
        self.assertEqual(max_page, 12)

        # 详情页结构化 JSON-LD
        detail_html = '''
        <html>
        <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Place",
          "name": "优品道",
          "address": {
            "@type": "PostalAddress",
            "addressLocality": "成都",
            "addressRegion": "青羊",
            "streetAddress": "青羊大道99号"
          },
          "geo": {
            "@type": "GeoCoordinates",
            "latitude": 30.668096,
            "longitude": 104.010963
          },
          "additionalProperty": [
            {"@type": "PropertyValue", "name": "建筑年代", "value": "2010年建成"},
            {"@type": "PropertyValue", "name": "物业公司", "value": "博瑞物业"}
          ],
          "offers": {
            "@type": "AggregateOffer",
            "lowPrice": 27000
          }
        }
        </script>
        </head>
        <body></body>
        </html>
        '''
        mock_and_etree.return_value = (detail_html, etree.HTML(detail_html))
        detailed_item = spider.parse_xiaoqu_detail(items[0])
        self.assertEqual(detailed_item.latitude, "30.668096")
        self.assertEqual(detailed_item.longitude, "104.010963")
        self.assertEqual(detailed_item.price, "27000元/㎡")
        self.assertEqual(detailed_item.details.get("建筑年代"), "2010年建成")
        self.assertEqual(detailed_item.details.get("物业公司"), "博瑞物业")

    def test_lianjia_api_token_builder(self):
        '''验证链家移动端 API SHA-1 与 Base64 签名生成算法'''
        spider = 链家(enable_api_accelerator=False)
        params = {'city_id': 110000, 'mobile_type': 'android'}
        token = spider._build_api_token(params)
        self.assertTrue(isinstance(token, str))
        self.assertGreater(len(token), 10)

    @patch('requests.post')
    def test_lianjia_get_city_tasks_via_api(self, mock_post):
        '''验证移动端 initData 接口返回商圈树解析与跨区 URL 去重'''
        spider = 链家(enable_api_accelerator=True)
        city_config_resp = {
            'data': {
                'city_config_all': {
                    'list': [{'city_id': 110000, 'city_name': '北京', 'abbr': 'bj'}]
                }
            }
        }
        city_info_resp = {
            'errno': 0,
            'data': {
                'city_info': {
                    'info': [{
                        'district': [
                            {
                                'district_name': '东城',
                                'bizcircle': [
                                    {'bizcircle_name': '东单', 'bizcircle_quanpin': 'dongdan'},
                                    {'bizcircle_name': '东直门', 'bizcircle_quanpin': 'dongzhimen'}
                                ]
                            },
                            {
                                'district_name': '朝阳',
                                'bizcircle': [
                                    {'bizcircle_name': '东单', 'bizcircle_quanpin': 'dongdan'},
                                    {'bizcircle_name': '望京', 'bizcircle_quanpin': 'wangjing'}
                                ]
                            }
                        ]
                    }]
                }
            }
        }
        mock_resp1 = MagicMock()
        mock_resp1.json.return_value = city_config_resp
        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = city_info_resp
        mock_post.side_effect = [mock_resp1, mock_resp2]

        tasks = spider.get_city_tasks_via_api('北京', 'https://bj.lianjia.com/')
        self.assertIsNotNone(tasks)
        # 4 个原始商圈中东单重复，去重后应为 3 个
        self.assertEqual(len(tasks), 3)
        urls = [t[2] for t in tasks]
        self.assertIn('https://bj.lianjia.com/xiaoqu/dongdan/', urls)
        self.assertIn('https://bj.lianjia.com/xiaoqu/dongzhimen/', urls)
        self.assertIn('https://bj.lianjia.com/xiaoqu/wangjing/', urls)

    @patch('requests.post')
    def test_lianjia_get_city_tasks_api_fallback(self, mock_post):
        '''验证移动端接口异常时自动平滑回退至网页分级抓取'''
        spider = 链家(enable_api_accelerator=True)
        mock_post.side_effect = Exception("API Server down")

        with patch.object(BaseSpider, 'get_city_tasks', return_value=[('区', '圈', 'http://test/')]) as mock_super:
            tasks = spider.get_city_tasks('北京', 'https://bj.lianjia.com/')
            mock_super.assert_called_once()
            self.assertEqual(tasks, [('区', '圈', 'http://test/')])

    def test_save_raw_html_snapshot(self):
        '''验证开启 save_raw_html 时原始 HTML 快照存盘功能'''
        spider = 链家(save_raw_html=True)
        spider.output_dir = self.test_dir
        html_content = "<html><body><h1>快照内容</h1></body></html>"
        spider.save_page_raw_html("北京", "test_plot_123", html_content)

        dump_path = os.path.join(self.test_dir, "链家", "raw_html", "北京", "test_plot_123.html")
        self.assertTrue(os.path.exists(dump_path))
        with open(dump_path, 'r', encoding='utf-8') as f:
            self.assertEqual(f.read(), html_content)



if __name__ == '__main__':
    unittest.main()
