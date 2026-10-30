'''
User-Agent、网络请求与代理模块单元测试
'''
import unittest
from unittest.mock import patch, MagicMock

import requests
from utils import ua, gather, proxies


class TestUaAndGather(unittest.TestCase):
    def test_random_ua(self):
        user_agents = set()
        for _ in range(50):
            agent = ua.get_random_ua()
            self.assertTrue(agent.startswith("Mozilla/5.0"))
            user_agents.add(agent)
        # 验证随机池多样性
        self.assertGreater(len(user_agents), 1)

    def test_random_headers(self):
        headers = ua.get_random_headers(referer="https://bj.lianjia.com/", extra_headers={'X-Custom': 'Val'})
        self.assertIn('User-Agent', headers)
        self.assertIn('Accept', headers)
        self.assertEqual(headers.get('Referer'), "https://bj.lianjia.com/")
        self.assertEqual(headers.get('X-Custom'), "Val")

    def test_proxy_manager(self):
        pm = proxies.ProxyManager(["http://1.1.1.1:80", "http://2.2.2.2:80"])
        self.assertEqual(len(pm), 2)
        p1 = pm.get_proxy()
        p2 = pm.get_proxy()
        p3 = pm.get_proxy()
        self.assertEqual(p1['http'], "http://1.1.1.1:80")
        self.assertEqual(p2['http'], "http://2.2.2.2:80")
        self.assertEqual(p3['http'], "http://1.1.1.1:80")  # 循环轮询

        pm.remove_proxy(p1)
        self.assertEqual(len(pm), 1)

    @patch('requests.get')
    def test_gather_safe_request_retry(self, mock_get):
        # 模拟前两次网络超时，第三次成功
        resp_success = MagicMock()
        resp_success.status_code = 200
        resp_success.content = b"<html>success</html>"
        mock_get.side_effect = [
            requests.exceptions.Timeout("Timeout 1"),
            requests.exceptions.ConnectionError("Conn error 2"),
            resp_success
        ]

        with patch('time.sleep'):  # 加速重试
            res = gather._safe_request("http://example.com", max_retries=3)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(mock_get.call_count, 3)

    @patch('requests.get')
    def test_gather_dummy_on_total_failure(self, mock_get):
        # 模拟全部重试均抛出异常
        mock_get.side_effect = requests.exceptions.ConnectionError("Fatal")
        with patch('time.sleep'):
            res = gather._safe_request("http://example.com", max_retries=2)
            self.assertEqual(res.status_code, 500)
            self.assertEqual(res.content, b"")

    @patch('requests.get')
    def test_gather_dynamic_ua_rotation(self, mock_get):
        '''验证爬虫请求中动态 UA 轮换已生效，且不破坏外部传入的 headers 原字典'''
        captured_uas = []
        def fake_get(url, **kw):
            captured_uas.append(kw.get('headers', {}).get('User-Agent'))
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"<html>ok</html>"
            return resp

        mock_get.side_effect = fake_get

        orig_headers = {'user-agent': 'old_static_ua', 'Custom': 'keep_me'}
        for _ in range(20):
            gather._safe_request("http://example.com", headers=orig_headers)

        # 1. 验证原字典未被篡改污染
        self.assertEqual(orig_headers['user-agent'], 'old_static_ua')
        self.assertEqual(orig_headers['Custom'], 'keep_me')

        # 2. 验证发出的请求实际注入了现代真实 UA 池中的 UA，且具有多样性
        self.assertTrue(all(ua.startswith("Mozilla/5.0") for ua in captured_uas))
        self.assertGreater(len(set(captured_uas)), 1)

    @patch('requests.get')
    def test_gather_proxy_rotation_on_retry(self, mock_get):
        '''验证失败重试时会自动从 ProxyManager 轮询切换新代理'''
        pm = proxies.ProxyManager(["http://10.0.0.1:8080", "http://10.0.0.2:8080"])
        used_proxies = []

        def fake_get(url, **kw):
            used_proxies.append(kw.get('proxies', {}).get('http'))
            if len(used_proxies) == 1:
                raise requests.exceptions.Timeout("Proxy 1 timeout")
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"<html>success with proxy 2</html>"
            return resp

        mock_get.side_effect = fake_get

        with patch('time.sleep'):
            res = gather._safe_request("http://example.com", proxy_manager=pm, max_retries=2)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(len(used_proxies), 2)
            self.assertEqual(used_proxies[0], "http://10.0.0.1:8080")
            self.assertEqual(used_proxies[1], "http://10.0.0.2:8080")

    def test_proxy_manager_thread_safety(self):
        '''验证多线程并发下 ProxyManager 的线程安全与防零除异常'''
        import threading
        pm = proxies.ProxyManager([f"http://192.168.1.{i}:80" for i in range(10)])

        def worker():
            for _ in range(50):
                p = pm.get_proxy()
                if p and int(p['http'].split('.')[-1].split(':')[0]) % 3 == 0:
                    pm.remove_proxy(p)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 确保安全执行未抛出 ZeroDivisionError 或 KeyError，即使被删空也能安全返回 None
        pm.remove_proxy(pm.get_proxy())
        self.assertIsInstance(len(pm), int)


if __name__ == '__main__':
    unittest.main()
