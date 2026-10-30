'''
全局配置
'''

# 通用 headers（兼容旧代码）
universal_headers = {
    'user-agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# 默认时间间隔（秒，兼容旧代码）
time_interval = 2

# 小区详情批量持久化阈值（兼容旧代码）
save_file_number = 20

# ---------------- 新增并发与反爬优化配置 ---------------- #

# 随机请求延迟区间（秒），防止固定频率被对端识别拦截
min_delay = 1.0
max_delay = 2.5

# 线程池并发工作线程数（建议 2~5，避免过高并发触发严格风控）
thread_pool_size = 3

# 数据持久化格式：'jsonl' (流式追加，推荐), 'csv', 'both' (同时输出两种格式)
storage_format = 'both'

# 请求超时时间（秒）
request_timeout = 15

# 最大重试次数
max_retries = 3

# 代理配置
enable_proxy = False
proxy_pool = []  # 可填入 ['http://ip:port', ...]

# 是否启用动态随机 User-Agent 请求头池
enable_random_ua = True

# ---------------- 链家移动端 API 加速与快照配置 ---------------- #

# 是否启用链家移动端配置接口极速获取城市架构 (initData 快速通道)
enable_lianjia_api_accelerator = True

# 是否在抓取小区详情时保存原始 HTML 快照到磁盘 (便于离线解析与容灾回放)
save_raw_html = False
