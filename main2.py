from flask import Flask, request, abort, jsonify, send_from_directory
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    MessageAction,
    TextMessage,
    FlexMessage,
    FlexContainer,    
    TemplateMessage,
    ButtonsTemplate,
    PostbackAction,
    PushMessageRequest,
)
from linebot.models import ImageSendMessage
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    
    FollowEvent,
    UnfollowEvent,
    PostbackEvent,
)


from linebot.exceptions import LineBotApiError
import re
import requests
from dotenv import load_dotenv
from typing import Tuple
import os
import json
import random
import persistence as db
import time
import qrcode

from flask_cors import CORS

health_info = None

app = Flask(__name__)
CORS(app)

load_dotenv(".env")

webhook = os.getenv("WEBHOOK", "/")

# 這個沒在用，只當樣板
user_info = {
    "user_id": None,
    "name": None,
    "idNumber": None,
    "tel": None,
    "steptype": None,
    "step": 0,  # 用來追蹤步驟，0 表示尚未開始，1 表示請輸入姓名，2 表示請輸入身分證字號，以此類推
    "errcount": 0,  # 用來記錄錯誤次數, 太多次就改問候語不用再輸入了
    "register": False,
}

access_token = os.getenv("ACCESS_TOKEN")
secret = os.getenv("SECRET")

configuration = Configuration(access_token=access_token)
handler = WebhookHandler(secret)
with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)
import logging

# 增加日誌輸出
logging.basicConfig(level=logging.INFO)


# 建立操作提示選項
def create_operation_options():
    buttons_template = ButtonsTemplate(
        title="請問你要進行什麼操作？",
        text="請點擊以下選項",
        actions=[
            PostbackAction(label="開始集點", data="start"),
            PostbackAction(label="不需要操作", data="logout"),
        ],
    )

    template_message = TemplateMessage(
        alt_text="請問你要進行什麼操作？", template=buttons_template
    )
    return template_message


# 產生進度條
def progress_bar(title: str, msg: str, current: int, max: int) -> str:
    # 計算進度條的長度
    progress = int(float(current) / float(max) * 100)
    if progress > 100:
        progress = 100

    # 定義 Flex Message 的 JSON 格式
    data = {
        "type": "carousel",
        "contents": [
            {
                "type": "bubble",
                "size": "kilo",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#27ACB2",
                    "paddingTop": "19px",
                    "paddingAll": "12px",
                    "paddingBottom": "16px",
                    "contents": [
                        {
                            "type": "text",
                            "text": title,
                            "color": "#FFFFFF",
                            "size": "md",
                            "align": "start",
                            "gravity": "center",
                        },
                        {
                            "type": "text",
                            "text": str(current) + "/" + str(max),
                            "color": "#ffffff",
                            "align": "start",
                            "size": "xs",
                            "gravity": "center",
                            "margin": "lg",
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {
                                    "type": "box",
                                    "layout": "vertical",
                                    "contents": [{"type": "filler"}],
                                    "width": str(progress) + "%",
                                    "backgroundColor": "#0D8186",
                                    "height": "8px",
                                }
                            ],
                            "backgroundColor": "#9FD8E3A0",
                            "height": "8px",
                            "margin": "sm",
                        },
                    ],
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "flex": 1,
                    "contents": [
                        {
                            "type": "text",
                            "text": msg,
                            "color": "#8C8C8C",
                            "size": "sm",
                            "wrap": True,
                        }
                    ],
                },
                "styles": {"footer": {"separator": False}},
            }
        ],
    }

    return data


# 主動推送訊息給使用者
def send_operation_options(line_bot_api, user_id):
    print(user_id)
    buttons_template = ButtonsTemplate(
        title="請問你要進行什麼操作？",
        text="請點擊以下選項",
        actions=[
            PostbackAction(label="開始集點", data="start"),
            PostbackAction(label="不需要操作", data="logout"),
        ],
    )

    template_message = TemplateMessage(
        alt_text="請問你要進行什麼操作？", template=buttons_template
    )
    line_bot_api.push_message_with_http_info(
        PushMessageRequest(to=user_id, messages=[template_message])
    )


# 主動推送訊息給使用者
def send_other_operation_options(line_bot_api, user_id):
    buttons_template = ButtonsTemplate(
        title="請問你還需要處理其他項目嗎？",
        text="請點擊以下選項",
        actions=[
            PostbackAction(label="生理監測", data="monitor"),
            PostbackAction(label="AI衛教", data="educate"),
            PostbackAction(label="運動", data="exercise"),
            PostbackAction(label="登出", data="logout"),
        ],
    )
    template_message = TemplateMessage(
        alt_text="請問你還需要處理其他項目嗎？", template=buttons_template
    )
    line_bot_api.push_message_with_http_info(
        PushMessageRequest(to=user_id, messages=[template_message])
    )


@app.route(webhook, methods=["POST"])
def linebot():

    # get X-Line-Signature header value
    signature = request.headers["X-Line-Signature"]

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except Exception as e:
        app.logger.error(f"Error: {e}")

    return "OK"


# 檢查身分證字號格式
def check_id_number(idNumber) -> bool:
    return re.match(r"^[A-Za-z]\d{9}$", idNumber)


# 檢查電話號碼格式
def check_tel(tel) -> bool:
    return re.match(r"\d{10}", tel)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):

    user_id = event.source.user_id

    # 查詢使用者資料，取回前一次登入操作的資料
    qdata = db.query_data(user_id)
    if qdata is not None:
        user_info = qdata
    else:
        # 沒有就建一個新的使用者資料
        user_info = createUserInfo(user_id)
        db.insert_data(user_id, user_info)

    push_message = False
    msg_list, push_message = dispatch_type(user_id, event.message.text, user_info)

    if len(msg_list) <= 0:
        if user_info["register"] == False:
            # 推送建議註冊訊息給使用者
            return
        else:
            # 處理其他不明訊息
            msg_list = process_message(event.source.user_id, event.message.text)

    if len(msg_list) > 0:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            if push_message:
                line_bot_api.push_message_with_http_info(
                    PushMessageRequest(to=user_id, messages=msg_list)
                )
            else:
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=msg_list,
                    )
                )
    return


def createUserInfo(userid: str):
    info = {
        "user_id": userid,
        "name": None,
        "idNumber": None,
        "tel": None,
        "steptype": None,
        "step": 0,
        "errcount": 0,
        "register": False,
    }
    return info


# 根據前一次的操作，分派訊息到對應的處理流程
def dispatch_type(user_id: str, message: str, user_info) -> tuple[list, bool]:
    msg_list = []
    push_message = False
    
    # 使用者沒有前一個步驟
    if user_info["steptype"] == None:

        if message == "新會員":
            user_info["step"] = 1
            user_info["steptype"] = "新會員"
            db.update_data(user_id, user_info)
            msg_list.append(TextMessage(text="請輸入姓名"))
        elif message == "連結LINE集點":
            user_info["step"] = 1
            user_info["steptype"] = "連結LINE集點"
            db.update_data(user_id, user_info)
            msg_list.append(TextMessage(text="請輸入身分證字號"))
        elif message == "集點":
            response = requests.put(
                url="https://linebotapi-tgkg.onrender.com/add/healthMeasurement",
                json={"lineId": user_info["user_id"]},  # 傳遞的 JSON 資料
            )
            print(response.status_code)
            data = response.json()
            health_measurement = data.get("healthMeasurement")  # 使用 .get() 確保鍵存在
            if response.status_code == 200:

                flex = progress_bar("集點券", "目前集點進度", health_measurement, 15)
                msg_list.append(
                    FlexMessage(
                        alt_text="hello", contents=FlexContainer.from_dict(flex)
                    )
                )

                if health_measurement < 15:
                    reply_text = f"集點成功，加油!!"
                if health_measurement == 15:
                    reply_text = f"集滿囉!!!可以拿給志工確認換禮物囉~"
                if health_measurement > 15:
                    reply_text = "有持續量血壓很棒喔~"
                msg_list.append(TextMessage(text=reply_text))
            else:
                reply_text = "集點失敗！請稍後嘗試!"
                msg_list.append(TextMessage(text=reply_text))

    else:

        if user_info["steptype"] == "連結LINE集點":
            idNumber = message
            lineId = user_id

            if check_id_number(idNumber):
                try:
                    response = requests.post(
                        url="https://linebotapi-tgkg.onrender.com/linkLineID/",
                        json={"idNumber": idNumber, "lineId": lineId},
                    )
                    if response.status_code == 200:
                        reply_text = "連結成功"
                    else:
                        reply_text = "重複連結或錯誤，請確認!"
                except Exception as e:
                    print(f"Error during request: {e}")
                    reply_text = "請聯絡管理員"

                msg_list.append(TextMessage(text=reply_text))
                # 完成步驟後，重設步驟狀態（如果需要）
                user_info["steptype"] = None
                user_info["step"] = 0  # 重設步驟為0
                user_info["errcount"] = 0
                db.update_data(user_id, user_info)
            else:
                user_info["errcount"] += 1
                db.update_data(user_id, user_info)
                reply_text = (
                    "身分證字號格式錯誤，請輸入有效的身分證字號（1個字母 + 9個數字）"
                )
                msg_list.append(TextMessage(text=reply_text))

        elif user_info["steptype"] == "新會員":
            if user_info["step"] == 1:
                user_info["step"] = 2
                user_info["name"] = message
                db.update_data(user_id, user_info)
                reply_text = "請輸入身分證字號"
                msg_list.append(TextMessage(text=reply_text))
            elif user_info["step"] == 2:
                if check_id_number(message):
                    user_info["idNumber"] = message
                    user_info["step"] = 3
                    db.update_data(user_id, user_info)
                    reply_text = "請輸入電話號碼"
                    msg_list.append(TextMessage(text=reply_text))

                else:
                    user_info["errcount"] += 1
                    db.update_data(user_id, user_info)
                    reply_text = "格式錯誤！請輸入 1 個英文字母和 9 個數字。"
                    msg_list.append(TextMessage(text=reply_text))
            elif user_info["step"] == 3:
                user_info["tel"] = message
                user_info["step"] = 4
                db.update_data(user_id, user_info)
                # Create confirmation template message
                buttons_template = ButtonsTemplate(
                    title="請確認您的資料",
                    text=(
                        f"您的姓名是 {user_info['name']}、\n"
                        f"身份證字號是 {user_info['idNumber']}、\n"
                        f"電話是 {user_info['tel']}。\n請問是否正確？"
                    ),
                    actions=[
                        PostbackAction(label="是", data="correct"),
                        PostbackAction(label="否", data="incorrect"),
                    ],
                )
                template_message = TemplateMessage(
                    alt_text="確認資料", template=buttons_template
                )
                msg_list.append(template_message)

            elif user_info["step"] == 4:
                if check_id_number(message):
                    user_info["idNumber"] = message
                    try:
                        response = requests.get(
                            url="https://linebotapi-tgkg.onrender.com/search/",
                            json={"idNumber": user_info["idNumber"]},
                        )
                        print(response, user_info["idNumber"])
                        if response.status_code == 200:
                            # 成功後，清掉步驟並發送操作選項
                            user_info["steptype"] = None
                            user_info["step"] = 0  # 重設步驟為0
                            user_info["errcount"] = 0
                            db.update_data(user_id, user_info)
                            print("send_operation_options")
                            msg_list.append(create_operation_options())
                            push_message = True
                        else:
                            reply_text = "請註冊!!"
                            msg_list.append(TextMessage(text=reply_text))

                    except:
                        reply_text = "請聯絡管理員"
                        msg_list.append(TextMessage(text=reply_text))
                else:
                    user_info["errcount"] += 1
                    db.update_data(user_id, user_info)
                    reply_text = "登入步驟錯誤或身分證字號格式錯誤"
                    msg_list.append(TextMessage(text=reply_text))

    return msg_list, push_message


@handler.add(PostbackEvent)
def handle_postback(event):

    qdata = db.query_data(event.source.user_id)
    if qdata is not None:
        user_info = qdata
    else:
        print("No result found")
        user_info = createUserInfo(event.source.user_id)
        db.insert_data(event.source.user_id, user_info)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        tk = event.reply_token
        data = event.postback.data

        if data == "correct":
            try:
                response = requests.post(
                    url="https://linebotapi-tgkg.onrender.com/add_user/",
                    json={
                        "name": user_info["name"],
                        "idNumber": user_info["idNumber"],
                        "tel": user_info["tel"],
                    },  # 傳遞的 JSON 資料
                )
                if response.status_code == 200:
                    # Confirm registration completion
                    user_info["register"] = True
                    db.update_data(event.source.user_id, user_info)

                    reply_text = "註冊完成！請輸入身分證字號登入"
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)],
                        )
                    )
                else:
                    reply_text = "註冊失敗！請稍後嘗試!"
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)],
                        )
                    )
            except:
                reply_text = "請聯絡管理員"
                # Confirm registration completion
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
        elif data == "incorrect":
            # Reset user information if incorrect
            user_info = createUserInfo(event.source.user_id)
            user_info["steptype"] = "新會員"
            user_info["step"] = 1
            db.update_data(event.source.user_id, user_info)

            reply_text = "請重新輸入姓名"
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        elif data == "start":
            buttons_template = ButtonsTemplate(
                title="請問你要處理哪個項目？",
                text="請點擊以下選項",
                actions=[
                    PostbackAction(label="生理監測", data="monitor"),
                    PostbackAction(label="AI衛教", data="educate"),
                    PostbackAction(label="運動", data="exercise"),
                    PostbackAction(label="登出", data="logout"),
                ],
            )

            template_message = TemplateMessage(
                alt_text="請問你要進行什麼集點？", template=buttons_template
            )
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token, messages=[template_message]
                )
            )
        elif data == "logout":
            user_info["steptype"] = None
            user_info["step"] = 0
            user_info["errcount"] = 0
            db.update_data(event.source.user_id, user_info)

            reply_text = "登出成功"
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        elif data == "monitor":
            response = requests.put(
                url="https://linebotapi-tgkg.onrender.com/add/healthMeasurement",
                json={"idNumber": user_info["idNumber"]},  # 傳遞的 JSON 資料
            )
            if response.status_code == 200:
                reply_text = "集點完成"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
                send_other_operation_options(line_bot_api, user_info["user_id"])
            else:
                reply_text = "集點失敗！請稍後嘗試!"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
        elif data == "educate":
            response = requests.put(
                url="https://linebotapi-tgkg.onrender.com/add/healthEducation",
                json={"idNumber": user_info["idNumber"]},  # 傳遞的 JSON 資料
            )
            if response.status_code == 200:
                reply_text = "集點完成"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            else:
                reply_text = "集點失敗！請稍後嘗試!"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            send_other_operation_options(line_bot_api, user_info["user_id"])
        elif data == "exercise":
            response = requests.put(
                url="https://linebotapi-tgkg.onrender.com/add/exercise",
                json={"idNumber": user_info["idNumber"]},  # 傳遞的 JSON 資料
            )
            if response.status_code == 200:
                reply_text = "集點完成"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            else:
                reply_text = "集點失敗！請稍後嘗試!"
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            send_other_operation_options(line_bot_api, user_info["user_id"])


# 加入好友
@handler.add(FollowEvent)
def handle_follow(event):
    app.logger.info("Got Follow event:" + event.source.user_id)
    msg_list = []

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        try:
            profile = line_bot_api.get_profile(event.source.user_id)
            print(profile.display_name)
            welcometitle = "您好！歡迎使用健康小幫手，您看起來還不是我們會員，請選擇新會員或其他以獲得服務。"
            if profile.display_name:
                welcometitle = profile.display_name + welcometitle

            msg_list.append(TextMessage(text=welcometitle))

            buttons_template = ButtonsTemplate(
                title="服務選單",
                text="請點擊以下選項",
                actions=[
                    MessageAction(label="新會員", text="新會員"),
                    PostbackAction(label="其他", data="idontknow"),
                ],
            )

            template_message = TemplateMessage(
                alt_text="歡迎新朋友～", template=buttons_template
            )

            msg_list.append(template_message)

        except LineBotApiError as e:
            print(e.status_code)

        if len(msg_list) > 0:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=msg_list,
                )
            )


# 取消好友
@handler.add(UnfollowEvent)
def handle_unfollow(event):
    # 看法規政策 有時候可能需要刪除使用者資料
    app.logger.info("Got Unfollow event:" + event.source.user_id)


# 其他訊息的回應
def process_message(userid: str, msg: str) -> list:
    global health_info
    msg_list = []

    if msg != None and msg != "":
        if msg in health_info:
            msg_list.append(TextMessage(text=health_info[msg]))
        else:
            random_key, random_value = random.choice(list(health_info.items()))
            msg_list.append(TextMessage(text=random_value))

    return msg_list


# 讀取健康資訊
def load_health_info(config_name: str):
    global health_info

    try:
        fh = open(config_name, "rt", encoding='UTF-8')
    except:
        print(f"'{config_name}' not found")
        exit()

    filedata = fh.read()
    health_info = json.loads(filedata)

    fh.close()


#掃QRCode集點

# 生成隨機數字並創建 QR 碼
def generate_qrcode_for_user(user_id, reply_token):
    # 生成隨機數字
    random_number = random.random()
    filename = f'qrcode_{random_number}.png'
    filepath = os.path.join('static', filename)

    # 生成 QR Code 圖片
    img = qrcode.make(str(random_number))
    img.save(filepath)

    # 回應用戶
    send_qrcode_to_user(user_id, reply_token, filename)

def send_qrcode_to_user(user_id, reply_token, filename):
    # 用 Ngrok 提供的 URL 替換 'your-domain' 來提供圖片
    qrcode_url = f'https://4e70-122-99-57-239.ngrok-free.app/static/{filename}'

    # 構建 ImageSendMessage 來發送圖片給用戶
    image_message = ImageSendMessage(
        original_content_url=qrcode_url,
        preview_image_url=qrcode_url
    )

    # 透過 LINE API 發送消息
    line_bot_api.reply_message(
        reply_token,
        image_message
    )
    
    # 在 15 秒後刪除該圖片
    time.sleep(15)
    if os.path.exists(os.path.join('static', filename)):
        os.remove(os.path.join('static', filename))

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # 獲取用戶發送的訊息
    user_message = event.message.text
    user_id = event.source.user_id

    # 根據訊息內容進行處理
    if user_message.lower() == "掃碼":  # 用戶發送 "掃碼" 訊息觸發生成 QR Code
        generate_qrcode_for_user(user_id, event.reply_token)

@app.route('/static/<filename>')
def send_static(filename):
    return send_from_directory(os.path.join(app.root_path, 'static'), filename)



def main():

    db.init_db()
    load_health_info("bot_health_info.json")

    host_ip = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))  # 默認使用 5000，但優先使用環境變數 PORT

    if os.getenv("HTTPTYPE") == "https":
        cerfile = os.getenv("certfile")
        keyfile = os.getenv("keyfile")
        app.run(host=host_ip, port=port, ssl_context=(cerfile, keyfile))
    else:
        app.run(host=host_ip, port=port)


if __name__ == "__main__":
    main()  # 呼叫 main() 函式啟動應用


@app.route("/trigger", methods=["GET", "POST"])
def trigger_api():
    try:
        return "OKOK"
    except Exception as e:
        return "QQ"


# ngrok http http://127.0.0.1:5000