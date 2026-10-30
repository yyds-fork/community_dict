'''
网络请求与 HTML 解析工具
支持动态随机 User-Agent、智能重试退避、代理分发与超时控制
'''
import random
import time
import requests
from lxml import etree, html

from config import config
from utils import ua


def _safe_request(url, max_retries=None, session=None, proxy_manager=None, **kwargv):
    '''统一的网络请求处理函数，包含动态UA轮换、智能重试退避、代理分发与超时控制'''
    if max_retries is None:
        max_retries = getattr(config, 'max_retries', 3)
    timeout = kwargv.pop('timeout', getattr(config, 'request_timeout', 15))

    # 请求头处理：浅拷贝避免多线程修改同一字典，并注入动态现代真实 User-Agent
    raw_headers = kwargv.get('headers')
    if raw_headers is None:
        headers = ua.get_random_headers()
    else:
        headers = dict(raw_headers)
        if getattr(config, 'enable_random_ua', True):
            # 移除既有 UA 键，注入随机 UA 池中的现代 User-Agent
            for k in list(headers.keys()):
                if k.lower() == 'user-agent':
                    del headers[k]
            headers['User-Agent'] = ua.get_random_ua()
        elif 'user-agent' not in [k.lower() for k in headers.keys()]:
            headers['User-Agent'] = ua.get_random_ua()
    kwargv['headers'] = headers

    # 确定代理来源：优先使用传入的 proxy_manager，其次使用 proxies 模块中的全局单例
    if proxy_manager is None:
        from utils import proxies as proxies_mod
        if getattr(config, 'enable_proxy', False):
            proxy_manager = proxies_mod.global_proxy_manager

    client = session or requests
    last_exception = None

    for attempt in range(max_retries):
        current_proxy = None
        # 若开启代理且未显式指定静态代理，则每次重试从代理池轮询获取新代理
        if 'proxies' not in kwargv and proxy_manager and len(proxy_manager) > 0:
            current_proxy = proxy_manager.get_proxy()
            call_kwargv = dict(kwargv)
            call_kwargv['proxies'] = current_proxy
        else:
            call_kwargv = kwargv

        try:
            resp = client.get(url, timeout=timeout, **call_kwargv)
            # 检查最终 URL 是否被对端重定向至验证码或登录页
            final_url = getattr(resp, 'url', '')
            if any(block_word in final_url for block_word in ('login', 'captcha', 'passport', 'sec.lianjia.com')):
                print(f"⚠️ 请求被拦截重定向至风控页面: {url} -> {final_url}")
                # 触发风控重定向时视为请求失败，若有备用代理则剔除该失效代理
                if current_proxy and proxy_manager and len(proxy_manager) > 1:
                    proxy_manager.remove_proxy(current_proxy)
                continue

            if resp.status_code == 200:
                return resp
            elif resp.status_code in (301, 302):
                loc = resp.headers.get('Location', '')
                if any(block_word in loc for block_word in ('login', 'captcha', 'passport', 'sec.lianjia.com')):
                    print(f"⚠️ 触发访问拦截/登录重定向 [{resp.status_code}]: {url} -> {loc}")
                    if current_proxy and proxy_manager and len(proxy_manager) > 1:
                        proxy_manager.remove_proxy(current_proxy)
                    continue
                return resp
            else:
                print(f"HTTP 响应状态码: {resp.status_code} ({url})")
                return resp
        except (requests.RequestException, OSError) as e:
            last_exception = e
            if current_proxy:
                print(f"代理请求失败 ({current_proxy['http']}): {e}")
                if proxy_manager and len(proxy_manager) > 1:
                    proxy_manager.remove_proxy(current_proxy)
            wait_time = (attempt + 1) * 1.5
            print(f"连接失败 ({e})，正在第 {attempt + 1}/{max_retries} 次重试 (等待 {wait_time:.1f}s): {url}")
            time.sleep(wait_time)

    # 全部重试失败后，构造一个空的 Dummy Response 避免直接崩溃
    print(f"❌ 请求失败已达最大重试次数: {url}, 错误: {last_exception}")
    dummy = requests.Response()
    dummy.status_code = 500
    dummy._content = b""
    return dummy


def get_html(url, coding='UTF-8', **kwargv):
    '''访问网站获取 html 源码字符串'''
    res = _safe_request(url, **kwargv)
    if not res.content or res.status_code != 200:
        return ""
    return res.content.decode(coding, errors='ignore')


def get_html_to_etree(url, coding='UTF-8', **kwargv):
    '''访问网站获取 html，并转换为 etree 对象返回'''
    res = _safe_request(url, **kwargv)
    if not res.content or res.status_code != 200:
        return etree.HTML('<html><body></body></html>')
    html_text = res.content.decode(coding, errors='ignore')
    tree = etree.HTML(html_text)
    return tree if tree is not None else etree.HTML('<html><body></body></html>')


def get_html_and_etree(url, coding='UTF-8', **kwargv):
    '''访问网站获取 html，并同时返回 html 代码与 etree 对象'''
    res = _safe_request(url, **kwargv)
    if not res.content or res.status_code != 200:
        return "", etree.HTML('<html><body></body></html>')
    html_text = res.content.decode(coding, errors='ignore')
    tree = etree.HTML(html_text)
    if tree is None:
        tree = etree.HTML('<html><body></body></html>')
    return html_text, tree


def etree2html(etree_obj, coding='UTF-8'):
    '''将 etree 对象转换为 html 字符串'''
    return html.tostring(etree_obj).decode(coding)
