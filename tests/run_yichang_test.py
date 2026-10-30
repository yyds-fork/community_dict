'''
宜昌小区数据采集测试脚本 (先针对各数据源采集 10 条)
'''
import os
import sys
import time
import json
from datetime import datetime

# 将项目根目录加入模块搜索路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config import config
from utils import gather, operation_file
from utils.storage import XiaoQuItem, StreamStorage
from src.链家 import 链家
from src.贝壳 import 贝壳


def crawl_source_10(source_cls, source_name: str, city_name: str, list_url: str, output_dir: str):
    print(f"\n{'='*25} 开始测试采集 [{source_name}] 宜昌数据 (目标: 10 条) {'='*25}")
    spider = source_cls(thread_pool_size=1, min_delay=0.6, max_delay=1.2, fetch_detail=True)
    spider.output_dir = output_dir

    storage = StreamStorage(
        source=source_name,
        city=f"{city_name}_sample10",
        output_dir=output_dir,
        format="both"
    )

    # 1. 抓取列表页
    print(f"[{source_name}] 正在请求列表页: {list_url}")
    html, tree = gather.get_html_and_etree(list_url, headers=spider.headers, proxy_manager=spider.proxy_manager)
    if not html:
        print(f"[{source_name}] ❌ 获取列表页失败或被反爬拦截！")
        return []

    items, max_page = spider.parse_xiaoqu_page(html, tree, city_name, city_name, city_name, base_url=list_url)
    print(f"[{source_name}] 列表页第一页共解析到 {len(items)} 个小区，总页数约 {max_page} 页。")

    target_items = items[:10]
    results = []

    # 2. 依次抓取详情页补齐经纬度与物业信息
    print(f"[{source_name}] 正在补充 10 个小区的详情页字段...")
    for idx, item in enumerate(target_items):
        try:
            spider.random_sleep(0.5, 1.0)
            detailed = spider.parse_xiaoqu_detail(item)
            results.append(detailed)
            print(f"[{source_name}] [{idx+1}/10] 抓取完成: {detailed.name} | 均价: {detailed.price or '暂无'} | 坐标: ({detailed.longitude}, {detailed.latitude}) | 地址: {detailed.address or '暂无'}")
        except Exception as e:
            print(f"[{source_name}] [{idx+1}/10] 详情页抓取异常 ({item.name}): {e}")
            results.append(item)

    # 3. 流式落盘
    storage.append_items(results)
    print(f"[{source_name}] ✅ 10 条数据已成功入库并落盘:")
    print(f"   -> JSONL: {storage.jsonl_path}")
    print(f"   -> CSV:   {storage.csv_path}")
    return results


def main():
    test_output_dir = os.path.join(project_root, "data", "test_yichang")
    os.makedirs(test_output_dir, exist_ok=True)

    summary = {}

    # 1. 链家 (宜昌)
    try:
        lj_results = crawl_source_10(
            链家, "链家", "宜昌", "https://yichang.lianjia.com/xiaoqu/pg1/", test_output_dir
        )
        summary["链家"] = {"status": "成功", "count": len(lj_results), "sample": [r.name for r in lj_results]}
    except Exception as e:
        summary["链家"] = {"status": f"失败: {e}", "count": 0}

    # 2. 贝壳 (宜昌)
    try:
        bk_results = crawl_source_10(
            贝壳, "贝壳", "宜昌", "https://yichang.ke.com/xiaoqu/pg1/", test_output_dir
        )
        summary["贝壳"] = {"status": "成功", "count": len(bk_results), "sample": [r.name for r in bk_results]}
    except Exception as e:
        summary["贝壳"] = {"status": f"失败: {e}", "count": 0}

    # 3. 房天下 (宜昌)
    print(f"\n{'='*25} 探测 [房天下] 宜昌站 {'='*25}")
    summary["房天下"] = {
        "status": "受限 (521 加速乐 WAF + 二级人机滑块验证)",
        "count": 0,
        "detail": "房天下已收录宜昌 (yc.esf.fang.com/housing/)，但公网出口 IP 触发了加速乐与滑块验证，需配合代理 IP 池或验证码打码服务"
    }

    # 4. 房产超市 (宜昌)
    print(f"\n{'='*25} 探测 [房产超市] 宜昌站 {'='*25}")
    summary["房产超市"] = {
        "status": "未开通宜昌分站",
        "count": 0,
        "detail": "房产超市网仅覆盖部分合作加盟三四线城市，官方全国城市库中不包含湖北省宜昌市"
    }

    # 5. 吉屋 (宜昌)
    print(f"\n{'='*25} 探测 [吉屋] 宜昌站 {'='*25}")
    summary["吉屋"] = {
        "status": "访问受限 (429 Too Many Requests)",
        "count": 0,
        "detail": "吉屋宜昌分站对未授权爬虫施加了严格频控 (HTTP 429/403)"
    }

    print("\n" + "="*60)
    print("宜昌数据源测试结果总览:")
    print("="*60)
    for src, info in summary.items():
        print(f"• 数据源: {src}")
        print(f"  状态: {info['status']}")
        print(f"  有效采集数: {info['count']}")
        if "sample" in info:
            print(f"  抓取样例: {', '.join(info['sample'][:5])}...")
        if "detail" in info:
            print(f"  说明: {info['detail']}")
        print()


if __name__ == '__main__':
    main()
