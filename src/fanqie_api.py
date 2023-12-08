
import os

# 导入必要的模块
import re
from os import path
import time
import public as p

from cos_upload import cos_upload
# noinspection PyPackageRequirements
from qcloud_cos import CosServiceError
# noinspection PyPackageRequirements
from qcloud_cos import CosClientError


# 定义正常模式用来下载番茄小说的函数
def download(url: str, encoding: str, config: dict) -> tuple:
    title = None
    last_cid = None
    finished: int = -1  # 使用数字代表小说是否已完结，-1 代表未知，0 代表未完结，1 代表已完结

    try:

        # 提取书籍ID
        book_id = re.search(r'page/(\d+)', url).group(1)

        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0.0.0 "
            "Safari/537.36"
        )
        headers, title, content, chapters, finished = p.get_fanqie(url, ua)

        # 定义文件名
        file_path = path.join(config["save_dir"],
                              config["filename_format"].format(title=title, book_id=book_id))

        os.makedirs(config["save_dir"], exist_ok=True)

        last_cid = None

        try:
            # 遍历每个章节链接
            for chapter in chapters:
                time.sleep(config["speed_limit"] if config["speed_limit"] > 0.25 else 0.25)

                result = p.get_api(chapter, headers)

                if result is None:
                    continue
                else:
                    chapter_title, chapter_text, chapter_id = result

                last_cid = chapter_id

                # 在小说内容字符串中添加章节标题和内容
                content += f"\n\n\n{chapter_title}\n{chapter_text}"

            # 根据编码转换小说内容字符串为二进制数据
            data = content.encode(encoding, errors='ignore')

            # 保存文件
            with open(file_path, "wb") as f:
                f.write(data)

            print(f"小说《{title}》已保存到本地")

            cos_status = upload_cos(file_path, title, config)

            print(cos_status)

            status = "completed"

            return status, title, last_cid, finished

        except BaseException as e:
            # 捕获所有异常，及时保存文件
            # 根据转换小说内容字符串为二进制数据
            data = content.encode(encoding, errors='ignore')

            # 保存文件
            with open(file_path, "wb") as f:
                f.write(data)

            print(f"小说《{title}》下载失败：{e}")

            print(f"小说《{title}》已保存到本地（中断保存）")

            raise Exception(f"下载失败: {e}")

    except BaseException as e:
        print(f"小说《{title}》下载失败：{e}")
        return "failed", title, last_cid, finished


def upload_cos(file_path, title, config):

    # 通过配置文件判断是否上传到COS
    if config["upload"]["cos"]["enable"]:
        try:
            cos_upload(file_path, config)
            result = f"小说《{title}》，路径：{file_path}，已上传到COS"
        except AssertionError as e:
            result = f"上传小说《{title}》，路径：{file_path}，失败：{e}"
        except KeyError as e:
            result = f"上传小说《{title}》，路径：{file_path}，配置文件格式错误，失败：{e}"
        except FileNotFoundError as e:
            result = f"上传小说《{title}》，路径：{file_path}，文件未找到，失败：{e}"
        except CosServiceError as e:
            result = f"上传小说《{title}》，路径：{file_path}，COS服务错误，失败：{e}"
        except CosClientError as e:
            result = f"上传小说《{title}》，路径：{file_path}，COS客户端错误，失败：{e}"
        except Exception as e:
            result = f"上传小说《{title}》，路径：{file_path}，其它错误，失败：{e}"
    else:
        result = "未启用COS上传"

    return result


def update(url: str, encoding: str, start_id: str, file_path: str, config: dict) -> tuple:
    title = None
    chapter_id_now = start_id
    finished: int = 0

    if os.path.exists(file_path) is False:
        return "failed", chapter_id_now, finished

    try:

        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0.0.0 "
            "Safari/537.36"
        )
        headers, title, content, chapters, finished = p.get_fanqie(url, ua)

        last_cid = None
        # 找到起始章节的索引
        start_index = 0
        for i, chapter in enumerate(chapters):
            chapter_url_tmp = chapter.find("a")["href"]
            chapter_id_tmp = re.search(r"/reader/(\d+)", chapter_url_tmp).group(1)
            if chapter_id_tmp == start_id:  # 更新函数，所以前进一个章节
                start_index = i + 1
            last_cid = chapter_id_tmp

        # 判断是否已经最新
        if start_index >= len(chapters):
            return "completed", title, last_cid, finished

        with open(file_path, 'ab') as f:
            try:
                # 从起始章节开始遍历每个章节链接
                for chapter in chapters[start_index:]:

                    time.sleep(config["speed_limit"] if config["speed_limit"] > 0.25 else 0.25)

                    result = p.get_api(chapter, headers)

                    if result is None:
                        continue
                    else:
                        chapter_title, chapter_text, chapter_id_now = result

                    # 在小说内容字符串中添加章节标题和内容
                    content = f"\n\n\n{chapter_title}\n{chapter_text}"

                    # 根据编码转换小说内容字符串为二进制数据
                    data = content.encode(encoding, errors='ignore')

                    # 将数据追加到文件中
                    f.write(data)

                print(f"小说《{title}》已保存到本地，路径：{file_path}")

                cos_status = upload_cos(file_path, title, config)

                print(cos_status)

                status = "completed"

                return status, chapter_id_now, finished

            except BaseException as e:

                print(f"小说《{title}》更新失败：{e}")

                print(f"小说《{title}》已保存到本地（中断保存）")

                raise Exception(f"更新失败: {e}")

    except BaseException as e:
        print(f"小说《{title}》更新失败：{e}")
        return "failed", chapter_id_now, finished
