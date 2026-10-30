'''
文件读写操作工具类
提供 JSON、JSONL、CSV 等文件的安全读取与流式写入功能。
'''
import csv
import json
import os
from typing import List, Dict, Any


def read_json_file(file_name: str) -> dict:
    '''读取 json 文件中的数据'''
    if not str(file_name).endswith('.json'):
        return {}
    try:
        with open(file_name, 'r', encoding='utf-8') as f:
            content = json.load(f)
        return content if isinstance(content, dict) else {}
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return {}


def write_json_file(file_name: str, data_dict: Any):
    '''将字典类型的数据写入 json 文件中'''
    dir_name = os.path.dirname(file_name)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(file_name, 'w', encoding='utf-8') as f:
        json.dump(data_dict, f, ensure_ascii=False, indent=2)


def read_jsonl_file(file_name: str) -> List[Dict[str, Any]]:
    '''按行读取 jsonl 文件中的数据列表，具备单行容错能力'''
    results = []
    if not os.path.exists(file_name):
        return results
    try:
        with open(file_name, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
    except Exception as e:
        print(f"读取 jsonl 文件异常: {file_name}, {e}")
    return results


def append_jsonl_file(file_name: str, item_dict: Dict[str, Any]):
    '''单条数据流式追加写入 jsonl 文件'''
    dir_name = os.path.dirname(file_name)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(file_name, 'a', encoding='utf-8') as f:
        f.write(json.dumps(item_dict, ensure_ascii=False) + '\n')


def read_csv_file(file_name: str) -> List[Dict[str, Any]]:
    '''读取 CSV 文件为字典列表'''
    results = []
    if not os.path.exists(file_name):
        return results
    try:
        with open(file_name, 'r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                results.append(dict(row))
    except Exception as e:
        print(f"读取 CSV 异常: {file_name}, {e}")
    return results
