
import os

# 导入必要的模块
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
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
def fanqie_l(url, encoding, return_dict):

    try:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0.0.0 "
            "Safari/537.36"
        )
        headers = {
            "User-Agent": ua
        }

        # 提取书籍ID
        book_id = re.search(r'page/(\d+)', url).group(1)

        # 获取网页源码
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text

        # 解析网页源码
        soup = BeautifulSoup(html, "html.parser")

        # 获取小说标题
        title = soup.find("h1").get_text()
        # , class_ = "info-name"
        # 替换非法字符
        title = p.rename(title)

        # 获取小说信息
        info = soup.find("div", class_="page-header-info").get_text()

        # 获取小说简介
        intro = soup.find("div", class_="page-abstract-content").get_text()

        # 拼接小说内容字符串
        content = f"""使用 @星隅(xing-yv) 所作开源工具下载
开源仓库地址:https://github.com/xing-yv/fanqie-novel-download
Gitee:https://gitee.com/xingyv1024/fanqie-novel-download/
任何人无权限制您访问本工具，如果有向您提供代下载服务者未事先告知您工具的获取方式，请向作者举报:xing_yv@outlook.com

{title}
{info}
{intro}
    """

        # 获取所有章节链接
        chapters = soup.find_all("div", class_="chapter-item")

        # 定义文件名
        file_path = path.join('output', f'{title}_{book_id}.txt')

        os.makedirs("output", exist_ok=True)

        try:
            # 遍历每个章节链接
            for chapter in chapters:
                time.sleep(0.5)
                # 获取章节标题
                chapter_title = chapter.find("a").get_text()

                # 获取章节网址
                chapter_url = urljoin(url, chapter.find("a")["href"])

                # 获取章节 id
                chapter_id = re.search(r"/(\d+)", chapter_url).group(1)

                # 构造 api 网址
                api_url = (f"https://novel.snssdk.com/api/novel/book/reader/full/v1/?device_platform=android&"
                           f"parent_enterfrom=novel_channel_search.tab.&aid=2329&platform_id=1&group_id="
                           f"{chapter_id}&item_id={chapter_id}")
                # 尝试获取章节内容
                chapter_content = None
                retry_count = 1
                while retry_count < 4:  # 设置最大重试次数
                    try:
                        # 获取 api 响应
                        api_response = requests.get(api_url, headers=headers, timeout=5)

                        # 解析 api 响应为 json 数据
                        api_data = api_response.json()
                    except Exception as e:
                        if retry_count == 1:
                            print(f"错误：{e}")
                            print(f"{chapter_title} 获取失败，正在尝试重试...")
                        print(f"第 ({retry_count}/3) 次重试获取章节内容")
                        retry_count += 1  # 否则重试
                        continue

                    if "data" in api_data and "content" in api_data["data"]:
                        chapter_content = api_data["data"]["content"]
                        break  # 如果成功获取章节内容，跳出重试循环
                    else:
                        retry_count += 1  # 否则重试

                if retry_count == 4:
                    continue  # 重试次数过多后，跳过当前章节

                # 提取文章标签中的文本
                chapter_text = re.search(r"<article>([\s\S]*?)</article>", chapter_content).group(1)

                # 将 <p> 标签替换为换行符
                chapter_text = re.sub(r"<p>", "\n", chapter_text)

                # 去除其他 html 标签
                chapter_text = re.sub(r"</?\w+>", "", chapter_text)

                chapter_text = p.fix_publisher(chapter_text)

                # 在小说内容字符串中添加章节标题和内容
                content += f"\n\n\n{chapter_title}\n{chapter_text}"

            # 根据编码转换小说内容字符串为二进制数据
            data = content.encode(encoding, errors='ignore')

            # 保存文件
            with open(file_path, "wb") as f:
                f.write(data)

            return_dict[f'result1'] = f"小说《{title}》已保存到本地"

            cos_status = upload_cos(file_path, title)

            return_dict[f'result2'] = cos_status

            pass

        except BaseException as e:
            # 捕获所有异常，及时保存文件
            # 根据转换小说内容字符串为二进制数据
            data = content.encode(encoding, errors='ignore')

            # 保存文件
            with open(file_path, "wb") as f:
                f.write(data)

            return_dict[f'result3'] = f"小说《{title}》下载失败：{e}"

            return_dict[f'result4'] = f"小说《{title}》已保存到本地（中断保存）"

            raise Exception(f"下载失败: {e}")
            pass

    except BaseException as e:
        return_dict['error'] = str(e)


def upload_cos(file_path, title):

    # 通过环境变量判断是否上传到COS
    if os.getenv("IS_COS") == "True":
        try:
            cos_upload(file_path)
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
