"""
作者：星隅（xing-yv）

版权所有（C）2023 星隅（xing-yv）

本软件根据GNU通用公共许可证第三版（GPLv3）发布；
你可以在以下位置找到该许可证的副本：
https://www.gnu.org/licenses/gpl-3.0.html

根据GPLv3的规定，您有权在遵循许可证的前提下自由使用、修改和分发本软件。
请注意，根据许可证的要求，任何对本软件的修改和分发都必须包括原始的版权声明和GPLv3的完整文本。

本软件提供的是按"原样"提供的，没有任何明示或暗示的保证，包括但不限于适销性和特定用途的适用性。作者不对任何直接或间接损害或其他责任承担任何责任。在适用法律允许的最大范围内，作者明确放弃了所有明示或暗示的担保和条件。

免责声明：
该程序仅用于学习和研究Python网络爬虫和网页处理技术，不得用于任何非法活动或侵犯他人权益的行为。使用本程序所产生的一切法律责任和风险，均由用户自行承担，与作者和项目协作者、贡献者无关。作者不对因使用该程序而导致的任何损失或损害承担任何责任。

请在使用本程序之前确保遵守相关法律法规和网站的使用政策，如有疑问，请咨询法律顾问。

无论您对程序进行了任何操作，请始终保留此信息。
"""

import re
import os
import json
import multiprocessing
import queue
import threading
from multiprocessing import Pool
import time
# import fanqie_api as fa
from fanqie_api import download, update
from flask import Flask, request, jsonify, make_response, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime, timedelta
# 使用sqlite数据库替换黑名单和任务状态表
import sqlite3

with open("config.json", "r", encoding='utf-8') as conf:
    try:
        config = json.load(conf)
    except json.JSONDecodeError as conf_e:
        raise json.JSONDecodeError("配置文件格式不正确", conf_e.doc, conf_e.pos)

os.makedirs(config["save_dir"], exist_ok=True)

https = config["server"]["https"]["enable"]
cert_path = config["server"]["https"]["ssl_cert"]
key_path = config["server"]["https"]["ssl_key"]
try:
    start_hour = int(config["time_range"].split("-")[0])
    end_hour = int(config["time_range"].split("-")[1])
except ValueError:
    pass

if config["administrator"]["totp"]["enable"]:
    from pyotp import TOTP

    try:
        totp = TOTP(config["administrator"]["totp"]["secret"])
    except TypeError:
        raise TypeError("管理员TOTP密钥格式不正确")


app = Flask(__name__)
if config["reserve_proxy"] is False:
    from flask_cors import CORS
    CORS(app)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["360 per day", "180 per hour"]
)

# 创建并连接数据库
db = sqlite3.connect(config["database"], check_same_thread=False)

# 创建一个黑名单表
db.execute('''
CREATE TABLE IF NOT EXISTS blacklist
(ip TEXT PRIMARY KEY,
unblock_time TEXT);
''')

# 创建一个任务状态表
db.execute('''
CREATE TABLE IF NOT EXISTS novels
(id TEXT PRIMARY KEY,
name TEXT,
status TEXT,
last_cid TEXT,
last_update TEXT,
finished INTEGER);
''')


@app.before_request
def block_method():
    if request.method == 'POST':
        ip = get_remote_address()
        # 检查IP是否在黑名单中
        cur1 = db.cursor()
        cur1.execute("SELECT unblock_time FROM blacklist WHERE ip=?", (ip,))
        row = cur1.fetchone()
        if row is not None:
            # 检查限制是否已经解除
            unblock_time = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S.%f')
            if datetime.now() < unblock_time:
                response = make_response("Too many requests. You have been added to the blacklist for 1 hour.", 429)
                # 计算剩余的封禁时间（以秒为单位），并添加到'Retry-After'头部
                retry_after = int((unblock_time - datetime.now()).total_seconds())
                response.headers['Retry-After'] = str(retry_after)
                return response
            else:
                # 如果限制已经解除，那么从黑名单中移除这个IP
                cur1.execute("DELETE FROM blacklist WHERE ip=?", (ip,))
                db.commit()
        cur1.close()


@app.errorhandler(429)
def ratelimit_handler(_e):
    # 将触发限制的IP添加到黑名单中，限制解除时间为1小时后
    ip = get_remote_address()
    unblock_time = datetime.now() + timedelta(hours=1)
    cur0 = db.cursor()
    cur0.execute("INSERT OR REPLACE INTO blacklist VALUES (?, ?)", (ip, unblock_time.strftime('%Y-%m-%d %H:%M:%S.%f')))
    db.commit()
    response = make_response("Too many requests. You have been added to the blacklist for 1 hour.", 429)
    response.headers['Retry-After'] = str(3600)  # 1小时的秒数
    cur0.close()
    return response


def book_id_to_url(book_id):
    return 'https://fanqienovel.com/page/' + book_id


def url_to_book_id(url):
    return re.search(r"page/(\d+)", url).group(1)


# 定义爬虫类
class Spider:
    def __init__(self):
        # 初始化URL队列
        self.url_queue = queue.Queue()
        # 设置运行状态为True
        self.is_running = True

    @staticmethod
    def crawl(url):
        try:
            print(f"Crawling for URL: {url}")
            book_id = url_to_book_id(url)
            curm = db.cursor()
            curm.execute("SELECT finished FROM novels WHERE id=?", (book_id,))
            row = curm.fetchone()
            # 根据完结信息判断模式
            if row is not None and row[0] == 0:
                # 如果已有信息，使用增量更新模式
                with Pool(processes=1) as pool:
                    print("Incremental update mode.")
                    curm.execute("SELECT name, last_cid FROM novels WHERE id=?", (book_id,))
                    row = curm.fetchone()
                    title = row[0]
                    last_cid = row[1]
                    file_path = os.path.join(config["save_dir"],
                                             config["filename_format"].format(title=title, book_id=book_id))
                    print(f"File path: {file_path}")
                    res = pool.apply(update, (url, config["encoding"], last_cid, file_path, config))  # 运行函数
                    # 获取任务和小说信息
                    status, last_cid, finished = res
                    # 写入数据库
                    curm.execute("UPDATE novels SET last_cid=?, last_update=?, finished=? WHERE id=?",
                                 (last_cid, datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'), finished, book_id))
                    db.commit()
                    curm.close()
                    if status == "completed":
                        return "completed"
                    else:
                        return "failed"
            else:
                # 如果没有或者未成功，则普通下载
                with Pool(processes=1) as pool:
                    print("Normal download mode.")
                    res = pool.apply(download, (url, config["encoding"], config))  # 运行函数
                    # 获取任务和小说信息
                    status, name, last_cid, finished = res
                    # 写入数据库
                    curm.execute("UPDATE novels SET name=?, last_cid=?, last_update=?, finished=? WHERE id=?",
                                 (name, last_cid, datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'), finished, book_id))
                    db.commit()
                    curm.close()
                    if status == "completed":
                        return "True"
                    else:
                        return "False"
        except Exception as e:
            print(f"Error: {e}")
            return "False"

    def worker(self):
        # 当运行状态为True时，持续工作
        while self.is_running:
            try:
                # 从URL队列中获取URL
                url = self.url_queue.get(timeout=1)
                book_id = url_to_book_id(url)
                curn = db.cursor()
                curn.execute("UPDATE novels SET status=? WHERE id=?", ("进行中", book_id))
                db.commit()
                status = Spider.crawl(url)
                # 调用爬虫函数爬取URL，如果出错则标记为失败并跳过这个任务进行下一个
                if status == "True":
                    curn.execute("UPDATE novels SET status=? WHERE id=?", ("已完成", book_id))
                    db.commit()
                elif status == "completed":
                    curn.execute("UPDATE novels SET status=? WHERE id=?", ("已更新完成", book_id))
                    db.commit()
                elif status == "failed":
                    curn.execute("UPDATE novels SET status=? WHERE id=?", ("更新失败", book_id))
                    db.commit()
                else:
                    curn.execute("UPDATE novels SET status=? WHERE id=?", ("失败", book_id))
                    db.commit()
                curn.close()
                # 完成任务后，标记任务为完成状态
                self.url_queue.task_done()
            except queue.Empty:
                time.sleep(5)
                continue

    def start(self):
        # 启动工作线程
        threading.Thread(target=self.worker, daemon=True).start()

    def add_url(self, book_id):
        # 则将URL添加到队列中并返回成功信息
        cura = db.cursor()
        cura.execute("SELECT status, finished FROM novels WHERE id=?", (book_id,))
        row = cura.fetchone()
        if row is None or row[0] == "失败":
            self.url_queue.put(book_id_to_url(book_id))
            cura.execute("INSERT OR REPLACE INTO novels (id, status) VALUES (?, ?)", (book_id, "等待中"))
            db.commit()
            cura.close()
            return "此书籍已添加到下载队列"
        else:
            # 如果已存在，检查书籍是否已完结
            if row[1] == 1:
                cura.close()
                # 如果已完结，返回提示信息
                return "此书籍已存在且已完结，请直接前往下载"
            elif row[0] == "等待中" or row[0] == "进行中" or row[0] == "等待更新中":
                cura.close()
                # 如果正在下载，返回提示信息
                return "此书籍已存在且正在下载（如果你需要查询，请在“类型”中选择“查询”而不是“添加”）"
            else:
                cura.execute("SELECT last_update FROM novels WHERE id=?", (book_id,))
                row = cura.fetchone()
                last_update = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S.%f')

                # 如果上次时间距现在小于3小时，返回提示
                if datetime.now() - last_update < timedelta(hours=3):
                    return "此书籍已存在且上次更新距现在不足3小时，请稍后再试"

                # 如果未完结，返回提示信息并尝试更新
                self.url_queue.put(book_id_to_url(book_id))
                cura.execute("UPDATE novels SET status=? WHERE id=?", ("等待更新中", book_id))
                db.commit()
                cura.close()
                return "此书籍已存在，正在尝试更新"

    def stop(self):
        # 设置运行状态为False以停止工作线程
        self.is_running = False


# 创建爬虫实例并启动
spider = Spider()
spider.start()


@app.route('/api', methods=['POST'])
@limiter.limit(f"{config['limiter']['api']['per_minute']}/minute;"
               f"{config['limiter']['api']['per_hour']}/hour;"
               f"{config['limiter']['api']['per_day']}/day")  # 限制请求
def api():

    # 判断是否在限时范围内
    now = datetime.utcnow() + timedelta(hours=8)
    if config["time_range"] == "false":
        pass
    else:
        if not (start_hour <= now.hour < end_hour):
            return f"此服务只在{start_hour}点到{end_hour}点开放。", 503
    # 获取请求数据
    data = request.get_json()
    # 检查请求数据是否包含'action'和'id'字段，如果没有则返回400错误
    if 'action' not in data or 'id' not in data:
        return "Bad Request.The request is missing necessary json data.", 400
    if data['id'].isdigit():
        pass
    else:
        if 'fanqienovel.com/page' in data['id']:
            # noinspection PyBroadException
            try:
                data['id'] = re.search(r"page/(\d+)", data['id']).group(1)
            except Exception:
                return "你输入的不是书籍ID或正确的链接。", 400
        elif 'changdunovel.com' in data['id']:
            # noinspection PyBroadException
            try:
                data['id'] = re.search(r"book_id=(\d+)&", data['id']).group(1)
            except Exception:
                return "你输入的不是书籍ID或正确的链接。", 400
        else:
            return "你输入的不是书籍ID或正确的链接。", 400

    # 如果'action'字段的值为'add'，则尝试将URL添加到队列中，并返回相应的信息和位置
    if data['action'] == 'add':
        book_id = data['id']
        message = spider.add_url(book_id)
        url = book_id_to_url(book_id)
        position = list(spider.url_queue.queue).index(url) + 1 if url in list(spider.url_queue.queue) else None
        curq = db.cursor()
        curq.execute("SELECT status, last_update FROM novels WHERE id=?", (book_id,))
        row = curq.fetchone()
        curq.close()
        status = row[0] if row is not None else None
        if row is not None:
            last_update = row[1].split('.')[0] if row[1] is not None else None
        else:
            last_update = None
        if last_update is not None:
            status = status + " " + last_update.split(".")[0]
        return jsonify({'message': message, 'position': position, 'status': status})

    # 如果'action'字段的值为'query'，则检查URL是否在队列中，并返回相应的信息和位置或不存在的信息
    elif data['action'] == 'query':
        book_id = data['id']
        url = book_id_to_url(book_id)
        position = list(spider.url_queue.queue).index(url) + 1 if url in list(spider.url_queue.queue) else None
        curw = db.cursor()
        curw.execute("SELECT status, last_update FROM novels WHERE id=?", (book_id,))
        row = curw.fetchone()
        curw.close()
        status = row[0] if row is not None else None
        if row is not None:
            last_update = row[1].split('.')[0] if row[1] is not None else None
        else:
            last_update = None
        if last_update is not None:
            status = status + " " + last_update
        return jsonify({'exists': status is not None, 'position': position, 'status': status})

    else:
        return "Bad Request.The value of ‘action’ can only be ‘add’ or ‘query’.", 400


@app.route('/list')
@limiter.limit(f"{config['limiter']['list']['per_minute']}/minute;"
               f"{config['limiter']['list']['per_hour']}/hour;"
               f"{config['limiter']['list']['per_day']}/day")  # 限制请求
def file_list():
    folder_path = config["save_dir"]
    files = os.listdir(folder_path)
    # 按最后修改时间排序
    files.sort(key=lambda x: os.path.getmtime(os.path.join(folder_path, x)), reverse=True)
    file_links = ['<a href="/download/{}">{}</a>'.format(f, f) for f in files]
    return '<html><body>{}</body></html>'.format('<br>'.join(file_links))


@app.route('/download/<path:filename>')
@limiter.limit(f"{config['limiter']['download']['per_minute']}/minute;"
               f"{config['limiter']['download']['per_hour']}/hour;"
               f"{config['limiter']['download']['per_day']}/day")  # 限制请求
def download_file(filename):
    directory = os.path.abspath(config["save_dir"])
    try:
        return send_from_directory(directory, filename, as_attachment=True)
    except FileNotFoundError:
        return "File not found.", 404


@app.route('/manage/<group>/<action>')
def manage(group, action):
    global config
    if config["administrator"]["enable"] is False:
        return "此功能已被禁用", 403
    if group == "check":
        if action == "check-passwd":
            try:
                passwd = request.args["passwd"]
            except KeyError:
                return "请求未携带密码，无权限访问", 403
            if passwd != config["administrator"]["password"]:
                return "密码错误，无权限访问", 403
            return "密码正确"
        elif action == "check-alive":
            return "alive"
        else:
            return "Bad Request.", 400
    try:
        passwd = request.args["passwd"]
    except KeyError:
        return "请求未携带密码，无权限访问", 403
    if passwd != config["administrator"]["password"]:
        return "密码错误，无权限访问", 403
    if config["administrator"]["totp"]["enable"]:
        try:
            totp_code = request.args["totp"]
        except KeyError:
            return "请求未携带TOTP验证码，无权限访问", 403
        if not totp.verify(totp_code):
            return "TOTP验证码错误，无权限访问", 403

    if group == "main":
        if action == "pause":
            spider.stop()
            return "已暂停"
        elif action == "start":
            spider.start()
            return "已启动"
        elif action == "status":
            return jsonify({'status': spider.is_running})
        elif action == "update-config":
            with open("config.json", "r", encoding='utf-8') as conf_u:
                try:
                    config = json.load(conf_u)
                except json.JSONDecodeError as conf_ue:
                    raise json.JSONDecodeError("配置文件格式不正确", conf_ue.doc, conf_ue.pos)
            return "已重新加载配置文件，部分配置需要重启服务器才能生效"
        else:
            return "Bad Request.", 400

    elif group == "tasks":
        curt = db.cursor()
        if action == "list-new-tasks":
            tasks_dict = {}
            curt.execute("SELECT id, status FROM novels ORDER BY ROWID DESC LIMIT 30", ())
            for i, row in enumerate(curt.fetchall()):
                tasks_dict[f'task{i}'] = {'id': row[0], 'status': row[1]}
            curt.close()
            return jsonify(tasks_dict)
        elif action == "list-all-tasks":
            if config["administrator"]["enable-list-all-tasks"] is False:
                return "此功能已被禁用", 403
            tasks_dict = {}
            curt.execute("SELECT id, status FROM novels")
            for i, row in enumerate(curt.fetchall()):
                tasks_dict[f'task{i}'] = {'id': row[0], 'status': row[1]}
            curt.close()
            return jsonify(tasks_dict)
        elif action == "list-tasks-all":
            # 已废弃的动作
            curt.close()
            return "请直接使用数据库管理工具查看", 410
        elif action == "clear-tasks":
            while not spider.url_queue.empty():
                try:
                    spider.url_queue.get_nowait()
                except queue.Empty:
                    break
            curt.close()
            return "已清空"
        else:
            curt.close()
            return "Bad Request.", 400

    elif group == "blacklist":
        curb = db.cursor()
        if action == "list-blacklist":
            curb.execute("SELECT * FROM blacklist")
            rows = curb.fetchall()
            curb.close()
            return jsonify(rows)
        elif action == "add-blacklist":
            ip = request.args["ip"]
            if "time" not in request.args:
                unblock_time = datetime.now() + timedelta(hours=1)
                curb.execute("INSERT OR REPLACE INTO blacklist VALUES (?, ?)",
                             (ip, unblock_time.strftime('%Y-%m-%d %H:%M:%S.%f')))
                db.commit()
                curb.close()
                return "已添加，解除时间为1小时后"
            else:
                try:
                    unblock_time = datetime.now() + timedelta(hours=int(request.args["time"]))
                    curb.execute("INSERT OR REPLACE INTO blacklist VALUES (?, ?)",
                                 (ip, unblock_time.strftime('%Y-%m-%d %H:%M:%S.%f')))
                    db.commit()
                    curb.close()
                    return f"已添加，解除时间为{request.args['time']}小时后"
                except ValueError:
                    curb.close()
                    return "时间格式不正确"
        elif action == "remove-blacklist":
            ip = request.args["ip"]
            curb.execute("DELETE FROM blacklist WHERE ip=?", (ip,))
            db.commit()
            curb.close()
            return f"已移除 {ip}"
        elif action == "clear-blacklist":
            # noinspection SqlWithoutWhere
            curb.execute("DELETE FROM blacklist")
            db.commit()
            curb.close()
            return "已清空黑名单"
        else:
            curb.close()
            return "Bad Request.", 400

    else:
        return "Bad Request.", 400


if __name__ == "__main__":
    # 如果你使用WSGI服务器，你可以将下面的内容放到你的WSGI服务器配置文件中
    multiprocessing.freeze_support()

    if https:
        print("HTTPS is enabled.")
        # 如果启用了HTTPS
        app.run(host=config["server"]["host"],
                port=config["server"]["port"],
                threaded=config["server"]["thread"],
                debug=config["server"]["debug"],
                ssl_context=(cert_path, key_path)
                )
    else:
        print("HTTPS is disabled.")
        # 如果没有启用HTTPS
        app.run(host=config["server"]["host"],
                port=config["server"]["port"],
                threaded=config["server"]["thread"],
                debug=config["server"]["debug"]
                )
