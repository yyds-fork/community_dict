'''
边界情况与容错异常路径测试
'''
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from lxml import etree
from src.base_spider import BaseSpider
from src.链家 import 链家
from utils.storage import XiaoQuItem, StreamStorage
from utils import operation_file


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_malformed_coords_parsing(self):
        # 异常坐标字符串：空值、非数字、反向经纬度、分号分隔
        item1 = XiaoQuItem(coords="")
        self.assertEqual(item1.latitude, "")
        self.assertEqual(item1.longitude, "")

        item2 = XiaoQuItem(coords="invalid,coordinates")
        self.assertEqual(item2.latitude, "")
        self.assertEqual(item2.longitude, "")

        item3 = XiaoQuItem(coords="116.4074;39.9042")
        self.assertEqual(item3.longitude, "116.4074")
        self.assertEqual(item3.latitude, "39.9042")

        item4 = XiaoQuItem(coords="39.9042,116.4074")
        self.assertEqual(item4.longitude, "116.4074")
        self.assertEqual(item4.latitude, "39.9042")

    def test_empty_districts_fallback(self):
        '''当城市无行政区划时，降级以城市为单元执行'''
        spider = 链家(thread_pool_size=1, storage_format="jsonl", min_delay=0, max_delay=0)
        spider.output_dir = self.test_dir

        with patch.object(spider, 'get_districts', return_value=[]):
            with patch.object(spider, 'crawl_bizcircle', return_value=5) as mock_crawl:
                spider.crawl_city("三亚", "https://sanya.lianjia.com/")
                mock_crawl.assert_called_once()
                args, _ = mock_crawl.call_args
                # 参数应为 (city_name, district_name, bizcircle_name, url, storage)
                self.assertEqual(args[0], "三亚")
                self.assertEqual(args[1], "三亚")
                self.assertEqual(args[2], "三亚")
                self.assertEqual(args[3], "https://sanya.lianjia.com/xiaoqu/")

    def test_empty_bizcircles_fallback(self):
        '''当行政区无商圈时，降级以区县为单元执行'''
        spider = 链家(thread_pool_size=1, storage_format="jsonl", min_delay=0, max_delay=0)
        spider.output_dir = self.test_dir

        fake_districts = [{'district_name': '延庆', 'district_url': 'https://bj.lianjia.com/xiaoqu/yanqing/'}]
        with patch.object(spider, 'get_districts', return_value=fake_districts):
            with patch.object(spider, 'get_bizcircles', return_value=[]):
                with patch.object(spider, 'crawl_bizcircle', return_value=3) as mock_crawl:
                    spider.crawl_city("北京", "https://bj.lianjia.com/")
                    mock_crawl.assert_called_once()
                    args, _ = mock_crawl.call_args
                    self.assertEqual(args[1], "延庆")
                    self.assertEqual(args[2], "延庆")
                    self.assertEqual(args[3], "https://bj.lianjia.com/xiaoqu/yanqing/")

    def test_corrupted_jsonl_and_csv_tolerance(self):
        '''测试文件损坏或格式非法时的宽容性'''
        bad_dir = os.path.join(self.test_dir, "链家")
        os.makedirs(bad_dir, exist_ok=True)
        bad_jsonl = os.path.join(bad_dir, "bad.jsonl")
        with open(bad_jsonl, 'w', encoding='utf-8') as f:
            f.write('{"name": "小区1", "url": "https://test.com/1"}\n')
            f.write('{broken json line\n')
            f.write('{"name": "小区2", "url": "https://test.com/2"}\n')

        records = operation_file.read_jsonl_file(bad_jsonl)
        # 应宽容跳过损坏行
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['name'], "小区1")
        self.assertEqual(records[1]['name'], "小区2")

        # StreamStorage 恢复时也能正常容忍
        storage = StreamStorage("链家", "bad", output_dir=self.test_dir)
        self.assertTrue(storage.is_url_visited("https://test.com/1"))
        self.assertTrue(storage.is_url_visited("https://test.com/2"))

    def test_total_count_capping(self):
        '''测试超过 100 页（如 3000+）时上限保护为 100'''
        spider = 链家()
        html_huge = '<h2 class="total fl">共找到<span>99999</span>个小区</h2><ul class="listContent"></ul>'
        tree = etree.HTML(html_huge)
        items, max_page = spider.parse_xiaoqu_page(html_huge, tree, "北京", "朝阳", "望京")
        self.assertEqual(max_page, 3334)  # ceil(99999 / 30)

        # 检查 crawl_bizcircle 中的 page loop 限制为 100
        storage = StreamStorage("链家", "上限测试", output_dir=self.test_dir)
        # 模拟前 100 页正常返回空列表结束，页面 HTML 存在（正常响应非网络中断）
        with patch.object(spider, 'parse_xiaoqu_page', return_value=([], 3334)):
            with patch('utils.gather.get_html_and_etree', return_value=("<html></html>", etree.HTML("<html></html>"))):
                count = spider.crawl_bizcircle("北京", "朝阳", "望京", "https://bj.lianjia.com/xiaoqu/wangjing/", storage)
                self.assertEqual(count, 0)
                # 正常响应结束时才标记为已完成
                self.assertTrue(storage.is_bizcircle_completed("朝阳", "望京"))

    def test_network_failure_does_not_mark_bizcircle_completed(self):
        '''网络请求彻底失败时，绝不能将商圈错误地标记为已完成，防止断点丢数据'''
        spider = 链家(thread_pool_size=1, storage_format="jsonl", min_delay=0, max_delay=0)
        spider.output_dir = self.test_dir
        storage = StreamStorage("链家", "北京", output_dir=self.test_dir)

        # 模拟首个请求网络失败返回空 HTML
        with patch('utils.gather.get_html_and_etree', return_value=("", etree.HTML("<html></html>"))):
            count = spider.crawl_bizcircle("北京", "朝阳", "望京", "https://bj.lianjia.com/xiaoqu/wangjing/", storage)
            self.assertEqual(count, 0)
            # 关键断言：失败绝不可被误标为完成
            self.assertFalse(storage.is_bizcircle_completed("朝阳", "望京"))

    def test_relative_url_normalization(self):
        '''列表页中提取的相对路径 URL 必须能正确拼装为绝对路径'''
        spider = 链家()
        html_relative = '''
        <ul class="listContent">
            <li class="clear xiaoquListItem">
                <div class="info">
                    <div class="title">
                        <a href="/xiaoqu/10099.html">测试相对小区</a>
                    </div>
                </div>
            </li>
            <li class="clear xiaoquListItem">
                <div class="info">
                    <div class="title">
                        <a href="//bj.lianjia.com/xiaoqu/10100.html">测试双斜杠小区</a>
                    </div>
                </div>
            </li>
        </ul>
        '''
        tree = etree.HTML(html_relative)
        items, _ = spider.parse_xiaoqu_page(
            html_relative, tree, "北京", "海淀", "中关村", base_url="https://bj.lianjia.com/xiaoqu/zhongguancun/"
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].url, "https://bj.lianjia.com/xiaoqu/10099.html")
        self.assertEqual(items[1].url, "https://bj.lianjia.com/xiaoqu/10100.html")

    def test_unsupported_city_check(self):
        '''贝壳网在新房域名或无二手房标识时应被正确识别为不支持'''
        from src.贝壳 import 贝壳
        beike = 贝壳()
        # .fang. 域名直接不支持
        self.assertFalse(beike.is_city_supported("测试新房城", "https://sanya.fang.ke.com/"))

        # 返回包含 plots_error_xpath 的页面识别为不支持
        with patch('utils.gather.get_html_to_etree') as mock_tree:
            mock_tree.return_value = etree.HTML('<div class="sec-list-nav clearfix"></div>')
            self.assertFalse(beike.is_city_supported("无二手房市", "https://none.ke.com/"))

    def test_detail_empty_elements(self):
        '''详情页关键信息缺失时的安全性'''
        spider = 链家()
        html_empty = '<html><body><div class="empty">无内容</div></body></html>'
        with patch('utils.gather.get_html_and_etree', return_value=(html_empty, etree.HTML(html_empty))):
            item = XiaoQuItem(name="空详情小区", url="https://test.com/xiaoqu/empty.html")
            res = spider.parse_xiaoqu_detail(item)
            self.assertEqual(res.address, "")
            self.assertEqual(res.address_chain, "")
            self.assertEqual(res.coords, "")
            self.assertEqual(res.details, {})

    def test_normalize_city_url_edge_cases(self):
        '''测试 URL 规范化函数针对各类脏数据的处理'''
        # 1. 包含分页占位符与参数的链家历史缓存 URL
        self.assertEqual(
            BaseSpider.normalize_city_url("https://aq.lianjia.com/xiaoqu/pg{page}/?from=rec"),
            "https://aq.lianjia.com/"
        )
        # 2. 贝壳历史协议相对路径 URL
        self.assertEqual(
            BaseSpider.normalize_city_url("//hf.ke.com"),
            "https://hf.ke.com/"
        )
        self.assertEqual(
            BaseSpider.normalize_city_url("//mas.ke.com/"),
            "https://mas.ke.com/"
        )
        # 3. 带有子路径和 http 协议的 URL
        self.assertEqual(
            BaseSpider.normalize_city_url("http://you.lianjia.com/qhxiaoqu/pg{page}/?from=rec"),
            "http://you.lianjia.com/"
        )
        self.assertEqual(
            BaseSpider.normalize_city_url("//lasa.fang.ke.com/loupan"),
            "https://lasa.fang.ke.com/"
        )
        # 4. 无协议头域名
        self.assertEqual(
            BaseSpider.normalize_city_url("bj.lianjia.com"),
            "https://bj.lianjia.com/"
        )
        # 5. 极端异常输入：空、全斜线、非法类型
        self.assertEqual(BaseSpider.normalize_city_url("//"), "")
        self.assertEqual(BaseSpider.normalize_city_url("///"), "")
        self.assertEqual(BaseSpider.normalize_city_url(""), "")
        self.assertEqual(BaseSpider.normalize_city_url("   "), "")
        self.assertEqual(BaseSpider.normalize_city_url(None), "")
        self.assertEqual(BaseSpider.normalize_city_url(12345), "")

        # 6. 伪协议与相对路径等非法输入必须被识别为空，杜绝非法请求
        self.assertEqual(BaseSpider.normalize_city_url("javascript:void(0)"), "")
        self.assertEqual(BaseSpider.normalize_city_url("javascript:;"), "")
        self.assertEqual(BaseSpider.normalize_city_url("#"), "")
        self.assertEqual(BaseSpider.normalize_city_url("about:blank"), "")
        self.assertEqual(BaseSpider.normalize_city_url("ftp://ftp.lianjia.com"), "")
        self.assertEqual(BaseSpider.normalize_city_url("mailto:admin@lianjia.com"), "")
        self.assertEqual(BaseSpider.normalize_city_url("://foo"), "")
        self.assertEqual(BaseSpider.normalize_city_url("https://:/"), "")
        self.assertEqual(BaseSpider.normalize_city_url("/xiaoqu/pg1/"), "")
        self.assertEqual(BaseSpider.normalize_city_url("localhost:8080"), "https://localhost:8080/")

    def test_dirty_city_cache_normalization(self):
        '''测试当本地存在脏数据缓存文件时，get_cities 自动清洗并持久化更新'''
        spider = 链家()
        cache_path = os.path.join(self.test_dir, "test_dirty_city.json")
        spider.citys_file_name = cache_path

        dirty_data = {
            "安庆": {"city_name": "安庆", "city_url": "https://aq.lianjia.com/xiaoqu/pg{page}/?from=rec"},
            "合肥": {"city_name": "合肥", "city_url": "//hf.ke.com"},
            "宜兴": {"city_name": "宜兴", "city_url": "//"}
        }
        operation_file.write_json_file(cache_path, dirty_data)

        # 读取并规范化
        cities = spider.get_cities()
        self.assertEqual(cities["安庆"]["city_url"], "https://aq.lianjia.com/")
        self.assertEqual(cities["合肥"]["city_url"], "https://hf.ke.com/")
        self.assertEqual(cities["宜兴"]["city_url"], "")

        # 验证文件是否已自动更新
        saved_data = operation_file.read_json_file(cache_path)
        self.assertEqual(saved_data["安庆"]["city_url"], "https://aq.lianjia.com/")
        self.assertEqual(saved_data["合肥"]["city_url"], "https://hf.ke.com/")
        self.assertEqual(saved_data["宜兴"]["city_url"], "")

    def test_crawl_city_with_dirty_url_fallback(self):
        '''测试即便直接传入脏 URL，降级生成的小区采集 URL 也必须是标准格式，绝不生成 corrupted URL'''
        spider = 链家(thread_pool_size=1, storage_format="jsonl", min_delay=0, max_delay=0)
        spider.output_dir = self.test_dir

        dirty_url = "https://aq.lianjia.com/xiaoqu/pg{page}/?from=rec"
        with patch.object(spider, 'get_districts', return_value=[]):
            with patch.object(spider, 'crawl_bizcircle', return_value=1) as mock_crawl:
                spider.crawl_city("安庆", dirty_url)
                mock_crawl.assert_called_once()
                args, _ = mock_crawl.call_args
                # 任务 URL 必须是标准合法的 https://aq.lianjia.com/xiaoqu/，不得带有 pg{page}
                self.assertEqual(args[3], "https://aq.lianjia.com/xiaoqu/")

    def test_crawl_city_with_invalid_url_skipped(self):
        '''测试传入空或完全无效的 city_url 时，安全跳过采集'''
        spider = 链家(thread_pool_size=1, storage_format="jsonl", min_delay=0, max_delay=0)
        spider.output_dir = self.test_dir

        with patch.object(spider, 'get_districts') as mock_districts:
            spider.crawl_city("无效城", "//")
            mock_districts.assert_not_called()

    def test_run_monitor_jobs_piped_out(self):
        '''测试 run.py 中 monitor_jobs 对管道输入 out 的即时响应与进程清理'''
        from run import monitor_jobs
        import io

        mock_proc1 = MagicMock()
        mock_proc1.is_alive.return_value = True
        mock_proc2 = MagicMock()
        mock_proc2.is_alive.return_value = True
        jobs = [(mock_proc1, "Job1"), (mock_proc2, "Job2")]

        fake_stdin = io.StringIO("out\n")
        monitor_jobs(jobs, stdin_stream=fake_stdin, poll_interval=0.01)

        # 验证两个进程均被 terminate
        mock_proc1.terminate.assert_called_once()
        mock_proc2.terminate.assert_called_once()
        mock_proc1.join.assert_called()
        mock_proc2.join.assert_called()

    def test_run_monitor_jobs_eof_handling(self):
        '''测试 run.py 中 monitor_jobs 遇到管道 EOF (/dev/null) 时的安全性，不挂起也不报错'''
        from run import monitor_jobs
        import io

        class DummyProcess:
            def __init__(self):
                self.alive_count = 2
            def is_alive(self):
                if self.alive_count > 0:
                    self.alive_count -= 1
                    return True
                return False
            def terminate(self):
                pass
            def join(self, timeout=None):
                pass

        p = DummyProcess()
        jobs = [(p, "Dummy")]

        # 空 stdin 模拟 EOF
        fake_stdin = io.StringIO("")
        monitor_jobs(jobs, stdin_stream=fake_stdin, poll_interval=0.01)

        # 进程自然结束，循环应正常退出
        self.assertFalse(p.is_alive())

    def test_run_monitor_jobs_real_pipe_out(self):
        '''测试 run.py 通过真实的 os.pipe 管道接收 out 输入'''
        from run import monitor_jobs

        r_fd, w_fd = os.pipe()
        rf = open(r_fd, 'r')
        with open(w_fd, 'w') as wf:
            wf.write("out\n")

        try:
            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            jobs = [(mock_proc, "PipedJob")]

            monitor_jobs(jobs, stdin_stream=rf, poll_interval=0.01)

            mock_proc.terminate.assert_called_once()
            mock_proc.join.assert_called()
        finally:
            rf.close()

    def test_run_monitor_jobs_keyboard_interrupt(self):
        '''测试 monitor_jobs 响应 KeyboardInterrupt 信号'''
        from run import monitor_jobs

        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        jobs = [(mock_proc, "SigIntJob")]

        fake_stdin = MagicMock()
        fake_stdin.closed = False
        fake_stdin.fileno.side_effect = KeyboardInterrupt

        monitor_jobs(jobs, stdin_stream=fake_stdin, poll_interval=0.01)

        mock_proc.terminate.assert_called_once()
        mock_proc.join.assert_called()

    def test_run_monitor_jobs_open_pipe_multiline(self):
        '''测试真实管道处于开启状态（未关闭写端）下突发多行输入 (如 hello\\nout\\n) 能立即响应并退出，不卡死在 select'''
        from run import monitor_jobs

        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"hello\nstatus\nout\n")

        rf = open(r_fd, 'r')
        try:
            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            jobs = [(mock_proc, "BurstJob")]

            monitor_jobs(jobs, stdin_stream=rf, poll_interval=0.01)

            mock_proc.terminate.assert_called_once()
            mock_proc.join.assert_called()
        finally:
            rf.close()
            os.close(w_fd)

    def test_run_monitor_jobs_no_trailing_newline_out(self):
        '''测试管道输入 out 但末尾未附带换行符直接到达 EOF 时能够正常响应退出'''
        from run import monitor_jobs

        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"out")
        os.close(w_fd)

        rf = open(r_fd, 'r')
        try:
            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            jobs = [(mock_proc, "NoNewlineJob")]

            monitor_jobs(jobs, stdin_stream=rf, poll_interval=0.01)

            mock_proc.terminate.assert_called_once()
            mock_proc.join.assert_called()
        finally:
            rf.close()

    def test_run_monitor_jobs_stringio_multiline(self):
        '''测试非 select 流 (如 StringIO) 中包含多行噪声后出现 out 能够即时响应'''
        from run import monitor_jobs
        import io

        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        jobs = [(mock_proc, "StringIOJob")]

        fake_stdin = io.StringIO("noise1\nnoise2\nout\n")
        monitor_jobs(jobs, stdin_stream=fake_stdin, poll_interval=0.01)

        mock_proc.terminate.assert_called_once()
        mock_proc.join.assert_called()


if __name__ == '__main__':
    unittest.main()

