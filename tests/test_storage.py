'''
存储与数据模型单元测试
'''
import os
import shutil
import tempfile
import threading
import unittest

from utils.storage import XiaoQuItem, StreamStorage, CSV_STANDARD_FIELDS
from utils import operation_file


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_xiaoqu_item_creation_and_coords(self):
        # 经纬度自动解析测试
        item = XiaoQuItem(
            city="北京",
            district="朝阳",
            bizcircle="安贞",
            name="安贞西里",
            url="https://bj.lianjia.com/xiaoqu/1001.html",
            price="85000",
            coords="116.412345,39.972345",
            details={"物业费": "2.5元/平米", "建筑年代": "1998年"}
        )
        self.assertEqual(item.longitude, "116.412345")
        self.assertEqual(item.latitude, "39.972345")

        # 字典序列化与扁平化
        d = item.to_dict()
        self.assertEqual(d['city'], "北京")
        self.assertEqual(d['details']['物业费'], "2.5元/平米")

        flat = item.to_flat_dict()
        self.assertEqual(flat['name'], "安贞西里")
        self.assertEqual(flat['物业费'], "2.5元/平米")

    def test_stream_storage_append_and_checkpoint(self):
        storage = StreamStorage(
            source="链家",
            city="测试市",
            output_dir=self.test_dir,
            format="both"
        )

        item1 = XiaoQuItem(
            source="链家",
            city="测试市",
            district="测试区A",
            bizcircle="商圈1",
            name="小区1",
            url="https://test.com/xiaoqu/1.html",
            price="50000"
        )
        item2 = XiaoQuItem(
            source="链家",
            city="测试市",
            district="测试区A",
            bizcircle="商圈1",
            name="小区2",
            url="https://test.com/xiaoqu/2.html",
            price="60000"
        )

        storage.append_item(item1)
        storage.append_item(item2)

        self.assertTrue(storage.is_url_visited("https://test.com/xiaoqu/1.html"))
        self.assertTrue(storage.is_url_visited("https://test.com/xiaoqu/2.html"))
        self.assertFalse(storage.is_url_visited("https://test.com/xiaoqu/999.html"))
        self.assertEqual(storage.get_visited_count(), 2)

        # 标记商圈完成
        self.assertFalse(storage.is_bizcircle_completed("测试区A", "商圈1"))
        storage.mark_bizcircle_completed("测试区A", "商圈1")
        self.assertTrue(storage.is_bizcircle_completed("测试区A", "商圈1"))

        # 验证文件是否正确生成
        jsonl_file = os.path.join(self.test_dir, "链家", "测试市.jsonl")
        csv_file = os.path.join(self.test_dir, "链家", "测试市.csv")
        self.assertTrue(os.path.exists(jsonl_file))
        self.assertTrue(os.path.exists(csv_file))

        # 读取并核验 jsonl
        records = operation_file.read_jsonl_file(jsonl_file)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['name'], "小区1")
        self.assertEqual(records[1]['name'], "小区2")

        # 读取并核验 csv
        csv_records = operation_file.read_csv_file(csv_file)
        self.assertEqual(len(csv_records), 2)
        self.assertEqual(csv_records[0]['name'], "小区1")

        # 验证新建实例是否能通过 checkpoint 恢复断点续传状态
        storage2 = StreamStorage(
            source="链家",
            city="测试市",
            output_dir=self.test_dir,
            format="both"
        )
        self.assertTrue(storage2.is_bizcircle_completed("测试区A", "商圈1"))
        self.assertTrue(storage2.is_url_visited("https://test.com/xiaoqu/1.html"))

    def test_concurrent_stream_writing(self):
        storage = StreamStorage(
            source="链家",
            city="并发市",
            output_dir=self.test_dir,
            format="both"
        )

        def worker(thread_idx):
            for i in range(25):
                item = XiaoQuItem(
                    source="链家",
                    city="并发市",
                    district="并发区",
                    bizcircle=f"商圈_{thread_idx}",
                    name=f"小区_{thread_idx}_{i}",
                    url=f"https://test.com/xiaoqu/{thread_idx}_{i}.html",
                    price="50000"
                )
                storage.append_item(item)

        threads = []
        for t in range(4):
            th = threading.Thread(target=worker, args=(t,))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()

        self.assertEqual(storage.get_visited_count(), 100)
        jsonl_file = os.path.join(self.test_dir, "链家", "并发市.jsonl")
        records = operation_file.read_jsonl_file(jsonl_file)
        self.assertEqual(len(records), 100)

    def test_url_normalization_dedup(self):
        '''测试 URL 结尾斜杠差异与空白符的规范化去重'''
        storage = StreamStorage(
            source="链家",
            city="去重市",
            output_dir=self.test_dir,
            format="jsonl"
        )
        item1 = XiaoQuItem(url="https://test.com/xiaoqu/1001/")
        storage.append_item(item1)

        # 变种 URL（无斜杠、带首尾空格）均应被成功识别为已访问
        self.assertTrue(storage.is_url_visited("https://test.com/xiaoqu/1001"))
        self.assertTrue(storage.is_url_visited("  https://test.com/xiaoqu/1001/  "))
        self.assertFalse(storage.is_url_visited("https://test.com/xiaoqu/1002"))

    def test_batch_append_items(self):
        '''测试批量 append_items 写入'''
        storage = StreamStorage(
            source="链家",
            city="批量市",
            output_dir=self.test_dir,
            format="both"
        )
        items = [
            XiaoQuItem(name=f"批量小区_{i}", url=f"https://test.com/{i}")
            for i in range(10)
        ]
        storage.append_items(items)
        self.assertEqual(storage.get_visited_count(), 10)
        records = operation_file.read_jsonl_file(storage.jsonl_path)
        self.assertEqual(len(records), 10)


if __name__ == '__main__':
    unittest.main()
