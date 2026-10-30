'''
按顺序爬取数据, 无效率要求
'''
import io
import multiprocessing
import os
import signal
import sys
import time

from src import 房产超市, 链家, 贝壳, 房天下


def worker(obj):
    name = obj.__class__.__name__
    print(f"[{name}] 启动采集任务...")
    try:
        obj.run()
        print(f"[{name}] 任务执行完成。")
    except Exception as e:
        print(f"[{name}] 运行异常: {e}")


def stop_workers(jobs, message="正在停止所有采集进程..."):
    if message:
        print(message)
    for p, name in jobs:
        try:
            if p.is_alive():
                p.terminate()
                p.join(timeout=2)
                if p.is_alive():
                    try:
                        p.kill()
                        p.join(timeout=1)
                    except Exception:
                        pass
                print(f"已停止 [{name}] 进程")
        except Exception as e:
            print(f"停止 [{name}] 进程异常: {e}")


def monitor_jobs(jobs, stdin_stream=None, poll_interval=1.0):
    '''监控采集进程，响应控制台/管道输入并在检测到 out 时优雅退出，安全兼容 EOF 与非 tty 运行'''
    try:
        if stdin_stream is None:
            stdin_stream = sys.stdin

        if stdin_stream and getattr(stdin_stream, 'isatty', lambda: False)():
            print("提示：输入 out 并回车可随时退出程序。")

        stdin_active = bool(stdin_stream and not getattr(stdin_stream, 'closed', False))

        def can_select(stream):
            try:
                fn = stream.fileno()
                return isinstance(fn, int) and fn >= 0
            except (io.UnsupportedOperation, AttributeError, OSError, ValueError):
                return False

        use_select = stdin_active and can_select(stdin_stream)
        stream_fd = stdin_stream.fileno() if use_select else None
        read_buffer = ""

        while any(p.is_alive() for p, _ in jobs):
            if stdin_active:
                if use_select:
                    try:
                        import select
                        rlist, _, _ = select.select([stream_fd], [], [], poll_interval)
                        if rlist:
                            chunk = os.read(stream_fd, 4096)
                            if not chunk:
                                # stdin 到达 EOF (管道读尽或重定向 /dev/null)，不再监控 stdin
                                stdin_active = False
                                if read_buffer.strip().lower() in ('out', 'exit', 'quit', 'q'):
                                    stop_workers(jobs, message="正在停止所有采集进程...")
                                    break
                            else:
                                read_buffer += chunk.decode('utf-8', errors='ignore')
                                lines = read_buffer.split('\n')
                                read_buffer = lines.pop()  # 保留末尾未完结行
                                should_stop = False
                                for line in lines:
                                    if line.strip().lower() in ('out', 'exit', 'quit', 'q'):
                                        should_stop = True
                                        break
                                if should_stop:
                                    stop_workers(jobs, message="正在停止所有采集进程...")
                                    break
                    except (io.UnsupportedOperation, OSError, ValueError):
                        stdin_active = False
                else:
                    # 不支持 select 的流 (如单元测试中的 StringIO) 逐行读取
                    should_stop = False
                    while stdin_active:
                        line = stdin_stream.readline()
                        if not isinstance(line, str) or line == "":
                            stdin_active = False
                            break
                        elif line.strip().lower() in ('out', 'exit', 'quit', 'q'):
                            should_stop = True
                            break
                    if should_stop:
                        stop_workers(jobs, message="正在停止所有采集进程...")
                        break
                    time.sleep(poll_interval)
            else:
                time.sleep(poll_interval)
    except (KeyboardInterrupt, EOFError):
        stop_workers(jobs, message="\n检测到退出信号，正在清理进程...")


def main():
    # 需要运行的采集器列表
    obj_list = [
        房产超市.房产超市(),
        链家.链家(),
        贝壳.贝壳(),
        房天下.房天下(),
    ]

    jobs = []
    for obj in obj_list:
        p = multiprocessing.Process(target=worker, args=(obj,))
        jobs.append((p, obj.__class__.__name__))
        p.start()

    print("=" * 50)
    print("所有采集进程已启动。")
    print("=" * 50)

    def sigterm_handler(signum, frame):
        stop_workers(jobs, message="\n检测到终止信号 (SIGTERM)，正在停止所有采集进程...")
        sys.exit(0)

    try:
        import signal
        signal.signal(signal.SIGTERM, sigterm_handler)
    except (ValueError, AttributeError):
        pass

    try:
        monitor_jobs(jobs)
    finally:
        stop_workers(jobs, message="")

    print("所有采集任务已退出。")


if __name__ == '__main__':
    main()
