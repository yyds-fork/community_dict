'''
代理获取与管理模块
'''
import socket
import threading
from config import config
from utils import gather


def test_proxy_connectivity(ip, port, timeout=3):
    '''使用 socket 检查代理连通性，替代已废弃的 telnetlib'''
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def get_proxies():
    '''从公开免费代理源采集并验证代理'''
    url = "https://www.kuaidaili.com/free/inha/{page}/"
    proxies = []

    for page in range(1, 4):
        try:
            etree = gather.get_html_to_etree(url.format(page=page))
            ips = etree.xpath('//td[@data-title="IP"]/text()')
            ports = etree.xpath('//td[@data-title="PORT"]/text()')
            for ip, port in zip(ips, ports):
                ip, port = ip.strip(), port.strip()
                print(f'检测 {ip}:{port}')
                if test_proxy_connectivity(ip, port, timeout=3):
                    proxy = {
                        'http': f'http://{ip}:{port}',
                        'https': f'http://{ip}:{port}',
                    }
                    proxies.append(proxy)
                else:
                    print(f'{ip}:{port} 超时或无法连接')
        except Exception as e:
            print(f'采集代理第{page}页异常: {e}')
    return proxies


class ProxyManager:
    '''代理管理器，支持多线程安全轮询与失败剔除'''
    def __init__(self, proxies=None):
        self._proxies = []
        self._index = 0
        self._lock = threading.Lock()
        if proxies:
            for p in proxies:
                self.add_proxy(p)

    def add_proxy(self, proxy_str):
        if isinstance(proxy_str, str):
            proxy = {'http': proxy_str, 'https': proxy_str}
        elif isinstance(proxy_str, dict):
            proxy = dict(proxy_str)
        else:
            return
        with self._lock:
            if proxy not in self._proxies:
                self._proxies.append(proxy)

    def get_proxy(self):
        '''线程安全轮询获取下一个可用代理'''
        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index = (self._index + 1) % len(self._proxies)
            return dict(proxy)

    def remove_proxy(self, proxy):
        '''剔除不可用的失效代理'''
        if not proxy:
            return
        with self._lock:
            if isinstance(proxy, str):
                target = {'http': proxy, 'https': proxy}
            else:
                target = proxy
            if target in self._proxies:
                self._proxies.remove(target)
            if self._proxies:
                self._index = self._index % len(self._proxies)
            else:
                self._index = 0

    def get_all(self):
        with self._lock:
            return [dict(p) for p in self._proxies]

    def __len__(self):
        with self._lock:
            return len(self._proxies)


# 全局默认代理管理器单例
global_proxy_manager = ProxyManager(
    getattr(config, 'proxy_pool', []) if getattr(config, 'enable_proxy', False) else None
)
