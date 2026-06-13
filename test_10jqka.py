"""Test 10jqka with correct API method names."""
import requests, json

FULL_COOKIE = (
    "Hm_lvt_69929b9dce4c22a060bd22d703b2a280=1781269169; "
    "HMACCOUNT=15EE57D89678B94E; "
    "_ga=GA1.1.1517565680.1781269171; "
    "u_ukey=A10702B8689642C6BE607730E11E6E4A; "
    "u_uver=1.0.0; "
    "u_dpass=%2Bjy5Wq%2BMN2fqW7u%2FVb%2F4igN6boVPwOYOvlcx%2B5%2Bjp91%2FQXDdaDnj1oMVPKi%2Byxi1Hi80LrSsTFH9a%2B6rtRvqGg%3D%3D; "
    "u_did=49B47198FDBC49959390CE206F3A04B6; "
    "u_ttype=WEB; ttype=WEB; "
    "user=MDpteF9jdWR6d2hvZ2Y6Ok5vbmU6NTAwOjg2NTY5MDc2NTo3LDExMTExMTExMTExLDQwOzQ0LDExLDQwOzYsMSw0MDs1LDEsNDA7MSwxMDEsNDA7MiwxLDQwOzMsMSw0MDs1LDEsNDA7OCwwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMSw0MDsxMDIsMSw0MDoxNjo6Ojg1NTY5MDc2NToxNzgxMjY5MTk3Ojo6MTc3NDI0NTk2MDo2MDQ4MDA6MDoxOTczZGYxZDExNGE4Mzk5ZGY1YTVkMjk4OTkzMTgxYzE6ZGVmYXVsdF81OjA%3D; "
    "userid=855690765; "
    "u_name=mx_cudzwhogf; "
    "escapename=mx_cudzwhogf; "
    "ticket=5f57fe62e0e716c099b5d56de74489b4; "
    "user_status=0; "
    "utk=5bd16002f2e9752810e5a209d7dabbd7; "
    "sess_tk=eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiIsImtpZCI6InNlc3NfdGtfMSIsImJ0eSI6InNlc3NfdGsifQ.eyJqdGkiOiJjMTgxMzE5OTk4ZDJhNWY1OWQzOWE4MTRkMWYxM2Q5NzEiLCJpYXQiOjE3ODEyNjkxOTcsImV4cCI6MTc4MTg3Mzk5Nywic3ViIjoiODU1NjkwNzY1IiwiaXNzIjoidXBhc3MuMTBqcWthLmNvbS5jbiIsImF1ZCI6IjIwMjAxMTE4NTI4ODkwNzIiLCJhY3QiOiJvZmMiLCJjdWhzIjoiNWMzZmE1M2Q1MzlkZjA3ZmNiM2M4NGRlMjM0Njc1N2UxZmY0Mzc5ZGMyMGI5OGU0ZjFjYjcwM2UzMmZhNGFlYSJ9.JSIFmfBf7li4b9tYvwZd9Huez5zuc7qicjAlHppxoPHaH-5Vtfx32Tq5roJe09TC1d39rrD_TAuQdJciH3ORHA; "
    "cuc=govl8l5qtmk6; "
    "Hm_lvt_78c58f01938e4d85eaf619eae71b4ed1=1781269203; "
    "__utma=156575163.1517565680.1781269171.1781269315.1781269315.1; "
    "__utmc=156575163; "
    "__utmz=156575163.1781269315.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); "
    "THSSESSID=3d2c01f8a9e856261b1e0c3136; "
    "Hm_lpvt_78c58f01938e4d85eaf619eae71b4ed1=1781269623; "
    "Hm_lpvt_69929b9dce4c22a060bd22d703b2a280=1781340960; "
    "_ga_H2RK0R0681=GS2.1.s1781340961$o3$g0$t1781340961$j60$l0$h0; "
    "_clck=1qttchs%7C2%7Cg6v%7C0%7C0; "
    "_clsk=mtppx11rtblp%7C1781341011987%7C1%7C1%7C; "
    "v=A24zoVOkiHRv6_wQ0_pTF29Lv881bzJ-RDPmTZg32nEsewBxAP-CeRTDNltr"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": FULL_COOKIE,
}

# 1. Try the api.php with proper method names
print("=" * 60)
print("1. api.php — method discovery")
# Common 10jqka method naming patterns
methods = [
    "group.getPostList",
    "group.getPost",
    "group.list",
    "newcircle.getPostList",
    "newcircle.getPost",
    "stock.getPostList",
    "stock.getDiscussion",
    "circle.getPostList",
    "discuss.getList",
    "group.discuss.list",
    "group.topic.list",
    "post.getList",
    "post.list",
]
for method in methods:
    try:
        resp = requests.get(
            "https://t.10jqka.com.cn/api.php",
            params={"method": method, "code": "603773", "page": "1", "count": "5"},
            headers=HEADERS, timeout=10
        )
        data = resp.json()
        errcode = data.get("errorCode") or data.get("errorcode")
        if errcode != -2:  # -2 = unknown method
            print(f"  {method}: {json.dumps(data, ensure_ascii=False)[:200]}")
    except Exception as e:
        pass

# 2. Try POST to group/api with different params
print()
print("=" * 60)
print("2. group/api — POST with various params")
for params_dict in [
    {"stockCode": "603773", "pageNo": "1", "pageSize": "5"},
    {"code": "603773", "page": "1", "size": "5"},
    {"symbol": "603773", "page": 1, "count": 5},
    {"stock_code": "603773", "page_no": 1, "page_size": 5},
]:
    try:
        resp = requests.post(
            "https://t.10jqka.com.cn/group/api/getPostList",
            data=params_dict,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=10
        )
        data = resp.json()
        print(f"  {params_dict}: {json.dumps(data, ensure_ascii=False)[:200]}")
    except Exception as e:
        print(f"  {params_dict}: ERROR {e}")

# 3. Try getPostList with the sess_tk as auth header
print()
print("=" * 60)
print("3. getPostList with auth header")
sess_tk = "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiIsImtpZCI6InNlc3NfdGtfMSIsImJ0eSI6InNlc3NfdGsifQ.eyJqdGkiOiJjMTgxMzE5OTk4ZDJhNWY1OWQzOWE4MTRkMWYxM2Q5NzEiLCJpYXQiOjE3ODEyNjkxOTcsImV4cCI6MTc4MTg3Mzk5Nywic3ViIjoiODU1NjkwNzY1IiwiaXNzIjoidXBhc3MuMTBqcWthLmNvbS5jbiIsImF1ZCI6IjIwMjAxMTE4NTI4ODkwNzIiLCJhY3QiOiJvZmMiLCJjdWhzIjoiNWMzZmE1M2Q1MzlkZjA3ZmNiM2M4NGRlMjM0Njc1N2UxZmY0Mzc5ZGMyMGI5OGU0ZjFjYjcwM2UzMmZhNGFlYSJ9.JSIFmfBf7li4b9tYvwZd9Huez5zuc7qicjAlHppxoPHaH-5Vtfx32Tq5roJe09TC1d39rrD_TAuQdJciH3ORHA"
resp = requests.post(
    "https://t.10jqka.com.cn/group/api/getPostList",
    data={"stockCode": "603773", "pageNo": "1", "pageSize": "5"},
    headers={
        **HEADERS,
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Bearer {sess_tk}",
    },
    timeout=10
)
print(f"  Status: {resp.status_code}, Body: {resp.text[:300]}")
