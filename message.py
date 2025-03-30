import random
import time
import json

import requests

cookies = {
    "abRequestId": "bc611ec4-47ed-5223-bc91-0bbfba80cc65",
    "a1": "1959936162fv967jtfc0iv00099nyp3ohfs92rtao50000422162",
    "webId": "4eaef2bdf4d12a9eeacb5d2b8b2c669e",
    "web_session": "030037a0e619232603531b0bad204a70fd6028",
    "gid": "yj2jjqKJfDxfyj2jjqKyKiF9JihjKWV9iS83M0lh888jjj28IvUqlJ8884JJyKJ8Jj4yi2yS",
    "loadts": "1742032816314",
    "xsecappid": "walle-ad",
    "x-user-id-sxt.xiaohongshu.com": "67bc150804f0000000000003",
    "customerClientId": "153952729589630",
    "acw_tc": "0a42278617424791581381333e22c8828e8c247777984dd90e6bf914cccfcd",
    "websectiga": "cffd9dcea65962b05ab048ac76962acee933d26157113bb213105a116241fa6c",
    "sec_poison_id": "e3920332-888f-4d5f-8954-3383946cc262",
    "customer-sso-sid": "68c517483891070912775170jsprrcg0nptebruh",
    "access-token-sxt.xiaohongshu.com": "customer.sxt.AT-68c517483891070912775173wndbrtlvszckosbb"
}


class SXT:

    def __init__(self, cookies):
        self.headers = {
            "authority": "sxt.xiaohongshu.com",
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "referer": "https://sxt.xiaohongshu.com/im/multiCustomerService?uba_pre=115.login..1742479176655&uba_ppre=115.home..1742479162478&uba_index=3",
            "sec-ch-ua": "\"Google Chrome\";v=\"107\", \"Chromium\";v=\"107\", \"Not=A?Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36",
            "x-subsystem": "sxt"
        }
        self.cookies = cookies
        self.user_id = self.cookies["x-user-id-sxt.xiaohongshu.com"]
        self.info = self.get_info()
        self.c_user_id = self.info["data"]["c_user_id"]
        self.platform = 1

    @classmethod
    def generate_uuid(cls):
        timestamp = int(time.time() * 1000)
        random_number = random.randint(10000000, 99999999)
        return f"{timestamp}-{random_number}"

    def get_info(self):
        url = "https://sxt.xiaohongshu.com/api-sxt/edith/ads/user/info"
        response = requests.get(url, headers=self.headers, cookies=self.cookies)
        return response.json()
    
    def has_new(self):
        url = "https://sxt.xiaohongshu.com/api-sxt/edith/intent/comment/new"
        response = requests.get(url, headers=self.headers, cookies=self.cookies)
        return response.json()

    def get_chats(self, is_active="false", limit="80"):
        url = "https://sxt.xiaohongshu.com/api-sxt/edith/chatline/chat"
        params = {
            "porch_user_id": self.user_id,
            "limit": limit,
            "is_active": is_active
        }
        response = requests.get(url, headers=self.headers, cookies=self.cookies, params=params)
        return json.dumps(response.json(), ensure_ascii=False)

    def get_chat_messages(self, customer_user_id, limit="20"):
        url = "https://sxt.xiaohongshu.com/api-sxt/edith/chatline/msg"
        params = {
            "porch_user_id": self.user_id,
            "customer_user_id": customer_user_id,
            "limit": limit
        }
        response = requests.get(url, headers=self.headers, cookies=self.cookies, params=params)
        return response.json()

    def read_chat(self, chat_user_id):
        url = "https://sxt.xiaohongshu.com/api-sxt/edith/chatline/chat/message/read"
        params = {
            "chat_user_id": chat_user_id
        }
        response = requests.get(url, headers=self.headers, cookies=self.cookies, params=params)
        return response.json()

    def send(self, receiver_id, content, message_type):
        url = "https://sxt.xiaohongshu.com/api-sxt/edith/chatline/msg"
        params = {
            "porch_user_id": self.user_id
        }
        data = {
            "sender_porch_id": self.user_id,
            "receiver_id": receiver_id,
            "content": content,
            "message_type": message_type,
            "uuid": self.generate_uuid(),
            "c_user_id": self.c_user_id,
            "platform": self.platform
        }
        response = requests.post(url, headers=self.headers, cookies=self.cookies, params=params, json=data)
        return response.json()

    def send_text(self, receiver_id, content):
        return self.send(receiver_id, content, "TEXT")

if __name__ == "__main__":
    sxt = SXT(cookies=cookies)
    sxt.send_text("67d24fee000000000e01235c", "测试消息")
