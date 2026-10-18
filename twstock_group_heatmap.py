"""
台股「族群」熱力圖 (Concept-Group Treemap)
==========================================
與官方 28 大產業別不同，這支程式用自訂的概念族群分類
（IC設計、ABF載板、AI伺服器/ODM、散熱…）來分群。

資料來源：台灣證券交易所 + 櫃買中心 OpenAPI（免金鑰）

安裝：
    pip install pandas plotly requests
執行：
    python twstock_group_heatmap.py
產出：
    twstock_group_heatmap.html

※ 族群對照表用「股票名稱」比對，程式會列出對不上的名稱，
   直接修改下方 GROUPS 即可，不必查代號。
"""

import sys

import pandas as pd
import plotly.express as px
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 共用連線：遇到斷線、逾時、5xx 自動重試（間隔 5、10、20、40 秒）
# 證交所對 GitHub 機房的 IP 偶爾會直接切斷連線，重試通常就能成功
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=4,
            backoff_factor=5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
    ),
)

# 自動模式下「資料還不是今天」時的結束代碼，workflow 會辨識它
EXIT_STALE = 75

# --------------------------------------------------------------------------
# 族群對照表：族群名稱 -> 股票名稱清單
# 想增刪族群或個股，改這裡就好
# --------------------------------------------------------------------------
GROUPS = {
    "晶圓代工": ["台積電", "聯電", "世界", "漢磊"],
    "IC設計": [
        "聯詠", "瑞昱", "義隆", "祥碩", "信驊", "原相", "新唐",
        "威盛", "凌陽", "達發", "矽力-KY", "譜瑞-KY",
    ],
    "IP/ASIC": ["聯發科", "創意", "世芯-KY", "力旺", "智原", "M31", "晶心科"],
    "記憶體": ["南亞科", "華邦電", "旺宏", "力積電", "群聯", "威剛", "晶豪科", "宜鼎"],
    "封測": [
        "日月光投控", "京元電子", "力成", "超豐", "華泰", "欣銓",
        "矽格", "南茂", "頎邦", "同欣電", "精材",
    ],
    "半導體設備/耗材": ["家登", "中砂", "旺矽", "精測", "亞翔", "帆宣", "弘塑", "辛耘", "崇越", "大量"],
    "矽晶圓/光罩": ["環球晶", "中美晶", "台勝科", "合晶", "光罩", "嘉晶"],
    "化合物半導體/功率": ["穩懋", "宏捷科", "全新", "聯亞", "強茂", "台半", "台亞", "朋程"],
    "ABF/IC載板": ["欣興", "南電", "景碩", "臻鼎-KY"],
    "PCB": ["華通", "金像電", "健鼎", "敬鵬", "嘉聯益", "燿華", "台郡", "定穎投控"],
    "CCL/PCB上游材料": ["台光電", "聯茂", "台燿", "南亞", "金居", "台玻", "台虹", "富喬"],
    "被動元件": ["國巨", "華新科", "晶技", "信昌電", "璟德", "禾伸堂", "蜜望實"],
    "光學鏡頭": ["大立光", "玉晶光", "亞光", "先進光", "佳凌"],
    "散熱": ["奇鋐", "雙鴻", "建準", "健策", "一詮", "力致", "泰碩"],
    "電源供應": ["台達電", "光寶科", "康舒", "僑威", "群電", "全漢"],
    "連接器/線材": ["嘉澤", "貿聯-KY", "正崴", "宣德", "良維", "健和興"],
    "網通": ["智邦", "啟碁", "中磊", "明泰", "友訊", "智易", "合勤控"],
    "AI伺服器/ODM": ["鴻海", "廣達", "緯創", "緯穎", "英業達", "技嘉", "神達", "勤誠", "川湖"],
    "組裝代工/EMS": ["和碩", "仁寶", "佳世達", "華碩"],
    "品牌PC/週邊": ["宏碁", "微星", "精英", "藍天", "撼訊", "麗臺"],
    "面板/驅動": ["友達", "群創", "彩晶", "TPK-KY", "瑞鼎", "天鈺"],
    "LED/光電": ["億光", "富采", "元太", "瑞儀", "中光電", "隆達"],
}

QUOTE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
# 同一份資料的另一個出口。實測這兩支的更新時間不同步，
# openapi 可能還停在前一天，www 這支已經是當天了，所以兩邊都要試。
QUOTE_URL_CSV = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data"
PROFILE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_QUOTE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

# 台股慣例：紅漲綠跌
TW_COLORSCALE = [
    (0.00, "#00b060"),
    (0.35, "#0d5c33"),
    (0.50, "#3a3a3a"),
    (0.65, "#7a1f1f"),
    (1.00, "#ff3b30"),
]
COLOR_RANGE = 7.0  # 顏色飽和的漲跌幅上限（±%）


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce"
    )


def _norm(s: pd.Series) -> pd.Series:
    """正規化股名：去掉 '*'、'創'、全形空白，方便比對。"""
    return (
        s.astype(str)
        .str.strip()
        .str.replace("*", "", regex=False)
        # 創新板標記是「-創」或「KY創」；不能無腦刪結尾的創，否則群創會變成群
        .str.replace(r"(?<=[-Y])創$", "", regex=True)
        .str.replace(r"-$", "", regex=True)
        .str.replace("\u3000", "", regex=False)
    )


def fetch(url: str) -> pd.DataFrame:
    r = SESSION.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return pd.DataFrame(r.json())


def roc_to_ad(d: str) -> str:
    """民國 1150901 -> 2026/09/01。看不懂的格式就原樣回傳。"""
    d = str(d).strip()
    if len(d) == 7 and d.isdigit():
        return f"{int(d[:3]) + 1911}/{d[3:5]}/{d[5:]}"
    return d


def _fetch_twse_latest() -> tuple[pd.DataFrame, str]:
    """從兩個來源各抓一次上市行情，採用日期較新的那一份。"""
    import datetime

    candidates = []

    try:
        j = fetch(QUOTE_URL)
        d = roc_to_ad(j["Date"].iloc[0]) if "Date" in j.columns and len(j) else "未知"
        candidates.append((d, j.rename(columns={"Code": "Code", "Name": "Name"}), "openapi"))
    except Exception as exc:  # noqa: BLE001
        print(f"  （openapi 來源失敗：{exc}）")

    try:
        import io

        r = SESSION.get(QUOTE_URL_CSV, timeout=30)
        r.raise_for_status()
        c = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")), dtype=str)
        c = c.rename(
            columns={
                "證券代號": "Code",
                "證券名稱": "Name",
                "收盤價": "ClosingPrice",
                "漲跌價差": "Change",
                "成交金額": "TradeValue",
            }
        )
        d = roc_to_ad(c["日期"].iloc[0]) if "日期" in c.columns and len(c) else "未知"
        candidates.append((d, c, "www"))
    except Exception as exc:  # noqa: BLE001
        print(f"  （www 來源失敗：{exc}）")

    if not candidates:
        raise RuntimeError("兩個上市資料來源都抓不到")

    # 日期新的優先
    def key(item):
        try:
            return datetime.datetime.strptime(item[0], "%Y/%m/%d")
        except ValueError:
            return datetime.datetime.min

    candidates.sort(key=key, reverse=True)
    date, df, src = candidates[0]
    if len(candidates) > 1 and candidates[0][0] != candidates[1][0]:
        print(f"  兩來源日期不同（{candidates[0][0]} vs {candidates[1][0]}），採用較新的 {src}")
    return df, date


def load_quotes() -> tuple[pd.DataFrame, str]:
    """抓上市 + 上櫃收盤行情，並回傳資料日期。

    ※ 這支 API 是「盤後」資料，收盤後才會更新成當天。
       開盤中或剛收盤時抓到的可能還是前一個交易日，
       所以一定要把日期顯示出來，不然看不出資料是舊的。
    """
    frames = []

    twse, data_date = _fetch_twse_latest()
    frames.append(
        pd.DataFrame(
            {
                "code": twse["Code"].astype(str).str.strip(),
                "name": twse["Name"].astype(str).str.strip(),
                "close": _to_num(twse["ClosingPrice"]),
                "change": _to_num(twse["Change"]),
                "value": _to_num(twse["TradeValue"]),
                "market": "上市",
            }
        )
    )

    # --- 上櫃 ---
    tpex_df = None
    try:
        tpex = fetch(TPEX_QUOTE_URL)
        cols = list(tpex.columns)

        def col(*keys: str) -> str:
            """依關鍵字找欄位，避免櫃買改欄位名稱就整批失敗。"""
            for c in cols:
                lc = str(c).lower()
                if any(k.lower() in lc for k in keys):
                    return c
            raise KeyError(f"找不到欄位 {keys}，實際欄位：{cols}")

        tpex_df = pd.DataFrame(
            {
                "code": tpex[col("SecuritiesCompanyCode", "Code", "代號")]
                .astype(str)
                .str.strip(),
                "name": tpex[col("CompanyName", "Name", "名稱")].astype(str).str.strip(),
                "close": _to_num(tpex[col("Close", "收盤")]),
                "change": _to_num(tpex[col("Change", "漲跌")]),
                "value": _to_num(tpex[col("TradingAmount", "TransactionAmount", "TradeValue", "成交金額")]),
                "market": "上櫃",
            }
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  （上櫃即時來源失敗：{exc}）")
        # 退回日期模式那條路徑，它已驗證可用
        try:
            tpex_df = load_tpex_by_date(data_date.replace("/", ""))
            print("  已改用備援來源取得上櫃資料")
        except Exception as exc2:  # noqa: BLE001
            print(f"  （上櫃備援也失敗，僅使用上市：{exc2}）")

    if tpex_df is not None and len(tpex_df):
        frames.append(tpex_df)
        print(f"  上櫃 {len(tpex_df)} 檔已併入")

    return pd.concat(frames, ignore_index=True), data_date


try:
    import chips as chips_mod
except ImportError:  # 沒放 chips.py 就略過籌碼區塊，主功能不受影響
    chips_mod = None

MI_INDEX_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"

# 盤勢總覽用：大盤指數與成交值、三大法人買賣超
FMTQIK_URL = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"
BFI82U_URL = "https://openapi.twse.com.tw/v1/fund/BFI82U"

LIMIT_PCT = 9.5  # 視為漲/跌停的門檻（台股漲跌幅上限 10%）

# 櫃買中心的歷史行情換過網址，兩個都試一次
TPEX_DATE_ENDPOINTS = [
    ("https://www.tpex.org.tw/www/zh-tw/afterTrading/otc", "new"),
    (
        "https://www.tpex.org.tw/web/stock/aftertrading/"
        "daily_close_quotes/stk_quote_result.php",
        "old",
    ),
]


def _pick(fields: list[str], *keywords: str) -> int:
    """依關鍵字找欄位位置。API 改版時欄位順序會變，寫死索引很容易壞。"""
    for i, f in enumerate(fields):
        name = str(f).replace(" ", "")
        if all(k in name for k in keywords):
            return i
    raise KeyError(f"找不到欄位：{keywords}，實際欄位為 {fields}")


def _extract_rows(payload: dict) -> tuple[list[str], list[list]]:
    """從櫃買回應中取出 (欄位名稱, 資料列)。相容 tables / aaData / data 幾種格式。"""
    if isinstance(payload.get("tables"), list):
        for t in payload["tables"]:
            fields = t.get("fields") or []
            if any("收盤" in str(f) for f in fields):
                return fields, t.get("data") or []
    if payload.get("aaData"):
        return payload.get("fields") or [], payload["aaData"]
    if payload.get("data"):
        return payload.get("fields") or [], payload["data"]
    raise RuntimeError("櫃買回應中找不到行情資料")


def load_tpex_by_date(date_str: str) -> pd.DataFrame:
    """抓指定日期（YYYYMMDD）的上櫃收盤行情。抓不到就丟出例外由呼叫端處理。"""
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    roc = f"{int(y) - 1911}/{m}/{d}"
    last_err = None

    for url, style in TPEX_DATE_ENDPOINTS:
        params = (
            {"date": f"{y}/{m}/{d}", "type": "EW", "response": "json"}
            if style == "new"
            else {"l": "zh-tw", "d": roc, "o": "json"}
        )
        try:
            r = SESSION.get(
                url, params=params, timeout=30,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            r.raise_for_status()
            fields, rows = _extract_rows(r.json())
            if not rows:
                raise RuntimeError("該日無資料")

            i_code = _pick(fields, "代號")
            i_name = _pick(fields, "名稱")
            i_close = _pick(fields, "收盤")
            i_chg = _pick(fields, "漲跌")
            try:
                i_val = _pick(fields, "成交金額")
            except KeyError:
                i_val = None

            raw = pd.DataFrame(rows)
            return pd.DataFrame(
                {
                    "code": raw[i_code].astype(str).str.strip(),
                    "name": raw[i_name].astype(str).str.strip(),
                    "close": _to_num(raw[i_close]),
                    "change": _to_num(raw[i_chg]),
                    "value": _to_num(raw[i_val]) if i_val is not None else 0.0,
                    "market": "上櫃",
                }
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue

    raise RuntimeError(f"上櫃資料抓取失敗：{last_err}")



def load_quotes_by_date(date_str: str) -> tuple[pd.DataFrame, str]:
    """抓指定日期（YYYYMMDD）的上市收盤行情。

    STOCK_DAY_ALL 只有最新一天，要查歷史得改用 MI_INDEX。
    注意這支的漲跌方向放在獨立的「漲跌(+/-)」欄位，且內容是 HTML，
    必須另外解析符號，不能直接把「漲跌價差」當成有號數字。
    """
    r = SESSION.get(
        MI_INDEX_URL,
        params={"response": "json", "date": date_str, "type": "ALLBUT0999"},
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    j = r.json()

    if j.get("stat") != "OK":
        raise RuntimeError(f"證交所回應：{j.get('stat')}（該日可能非交易日）")

    # 找出含個股收盤價的那張表
    table = None
    for t in j.get("tables", []):
        fields = t.get("fields", [])
        if "證券代號" in fields and "收盤價" in fields:
            table = t
            break
    if table is None:
        raise RuntimeError("找不到個股收盤資料表，API 格式可能已變更")

    raw = pd.DataFrame(table["data"], columns=table["fields"])

    # 漲跌方向：欄位內容像 <p style="color:red">+</p>，只取符號
    sign_col = raw["漲跌(+/-)"].astype(str)
    sign = sign_col.apply(lambda x: -1 if "-" in x else 1)
    change = _to_num(raw["漲跌價差"]).fillna(0) * sign

    quotes = pd.DataFrame(
        {
            "code": raw["證券代號"].astype(str).str.strip(),
            "name": raw["證券名稱"].astype(str).str.strip(),
            "close": _to_num(raw["收盤價"]),
            "change": change,
            "value": _to_num(raw["成交金額"]),
            "market": "上市",
        }
    )
    # 併入上櫃；抓不到只警告，不讓整支程式失敗
    try:
        tpex = load_tpex_by_date(date_str)
        quotes = pd.concat([quotes, tpex], ignore_index=True)
        print(f"  上櫃 {len(tpex)} 檔已併入")
    except Exception as exc:  # noqa: BLE001
        print(f"  （上櫃資料抓取失敗，僅使用上市：{exc}）")

    ad = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"
    return quotes, ad


def load_capital() -> pd.DataFrame:
    p = fetch(PROFILE_URL)
    return pd.DataFrame(
        {
            "code": p["公司代號"].astype(str).str.strip(),
            "capital": _to_num(p["實收資本額"]),
        }
    )


def build_dataframe(quotes: pd.DataFrame, capital: pd.DataFrame) -> pd.DataFrame:
    """依 GROUPS 篩選出族群成分股，算漲跌幅與市值。"""
    # 展開對照表
    mapping = pd.DataFrame(
        [(g, n) for g, names in GROUPS.items() for n in names],
        columns=["group", "want"],
    )
    mapping["key"] = _norm(mapping["want"])

    df = quotes.copy()
    df["key"] = _norm(df["name"])
    df = df.dropna(subset=["close", "change"])
    df = df[df["close"] > 0]

    merged = mapping.merge(df, on="key", how="left")

    missing = merged[merged["code"].isna()]["want"].tolist()
    if missing:
        print(f"⚠ 以下名稱在行情資料中找不到，已略過：{'、'.join(missing)}")

    out = merged.dropna(subset=["code"]).copy()

    prev = out["close"] - out["change"]
    out = out[prev > 0].copy()
    out["pct"] = out["change"] / (out["close"] - out["change"]) * 100

    if len(capital):
        out = out.merge(capital, on="code", how="left")
    else:
        out["capital"] = float("nan")
    # 市值只用於「族群強弱排行」的加權，不再決定格子大小，
    # 所以算不出市值的個股保留下來，不篩掉
    out["market_cap"] = out["capital"] / 10 * out["close"]
    out["market_cap"] = out["market_cap"].fillna(out["value"]).fillna(0)

    out["label"] = out["name"]
    out["cap_billion"] = out["market_cap"] / 1e8
    # 每檔格子一樣大：面積不再代表市值，族群大小 = 成分股檔數
    out["size"] = 1

    # 同一檔若被歸到多個族群，各自保留（例如台達電同時屬電源）
    return out.drop_duplicates(subset=["group", "code"]).reset_index(drop=True)


def fetch_index_info(data_date: str) -> dict:
    """大盤加權指數、漲跌點數、成交金額。抓不到就回空 dict，不影響其他內容。"""
    try:
        d = fetch(FMTQIK_URL)
        want = data_date.replace("/", "")
        want_roc = f"{int(want[:4]) - 1911}{want[4:]}"
        row = d[d["Date"].astype(str).str.strip() == want_roc]
        if row.empty:
            return {}
        r = row.iloc[0]
        return {
            "index": _to_num(pd.Series([r["TAIEX"]])).iloc[0],
            "change": _to_num(pd.Series([r["Change"]])).iloc[0],
            "amount": _to_num(pd.Series([r["TradeValue"]])).iloc[0],
        }
    except Exception:  # noqa: BLE001
        return {}


# 櫃買指數：櫃買中心換過網址，新舊各試一次（月資料，逐日一列）
TPEX_INDEX_ENDPOINTS = [
    ("https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex", "new"),
    (
        "https://www.tpex.org.tw/web/stock/aftertrading/"
        "daily_trading_index/st41_result.php",
        "old",
    ),
]


def fetch_otc_index(data_date: str) -> dict:
    """櫃買指數收盤與漲跌點數。抓不到就回空 dict，並在 log 印出原因。"""
    if "/" not in data_date:
        return {}
    y, m, d = data_date.split("/")
    roc_day = f"{int(y) - 1911}/{m}/{d}"
    errors = []

    for url, style in TPEX_INDEX_ENDPOINTS:
        params = (
            {"date": f"{y}/{m}/{d}", "response": "json"}
            if style == "new"
            else {"l": "zh-tw", "d": f"{int(y) - 1911}/{m}", "o": "json"}
        )
        try:
            r = SESSION.get(url, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()

            # 相容 tables / aaData / data 幾種格式
            fields, rows = [], []
            if isinstance(j.get("tables"), list) and j["tables"]:
                t = j["tables"][0]
                fields, rows = t.get("fields") or [], t.get("data") or []
            else:
                fields = j.get("fields") or []
                rows = j.get("aaData") or j.get("data") or []
            if not rows:
                raise RuntimeError("回應中沒有資料列")

            def find(*keys: str, default: int) -> int:
                for i, f in enumerate(fields):
                    if any(k in str(f) for k in keys):
                        return i
                return default

            # 舊版沒附欄位名稱時，欄位順序為：日期、成交股數、金額、筆數、櫃買指數、漲跌
            i_idx = find("指數", default=4)
            i_chg = find("漲跌", default=5)

            row = next(
                (rw for rw in rows if str(rw[0]).strip().replace(" ", "") == roc_day),
                None,
            )
            if row is None:
                raise RuntimeError(f"月資料中找不到 {roc_day}（可能尚未更新）")

            idx = _to_num(pd.Series([row[i_idx]])).iloc[0]
            chg = _to_num(pd.Series([row[i_chg]])).iloc[0]
            if pd.isna(idx) or pd.isna(chg):
                raise RuntimeError(f"數值解析失敗：{row}")
            print(f"  櫃買指數來源：{style}（{float(idx):,.2f}，{float(chg):+,.2f}）")
            return {"otc_index": float(idx), "otc_change": float(chg)}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{style} -> {exc}")

    print("  （櫃買指數抓取失敗，總覽將略過該項）")
    for e in errors:
        print(f"    - {e}")
    return {}


def _accumulate_inst(out: dict, name: str, buy: float, sell: float, net: float) -> None:
    """把單位名稱歸到外資／投信／自營商三類，買進、賣出、買賣超一起累加。"""
    name = name.strip()
    if "合計" in name:
        return
    # 外資自營商的金額已計入自營商，證交所合計不重複計算，這裡跟著排除。
    # 必須用開頭比對：「外資及陸資(不含外資自營商)」裡面也有這五個字。
    if name.startswith("外資自營商"):
        return

    # 判斷順序不能顛倒：「外資及陸資(不含外資自營商)」裡也有「自營」兩字，
    # 所以先看開頭是不是「自營商」。
    if name.startswith("自營商"):
        key = "自營商"
    elif "投信" in name:
        key = "投信"
    elif "外資" in name or "陸資" in name:
        key = "外資"
    else:
        return

    slot = out.setdefault(key, {"buy": 0.0, "sell": 0.0, "net": 0.0})
    slot["buy"] += buy / 1e8
    slot["sell"] += sell / 1e8
    slot["net"] += net / 1e8


def fetch_institutional(date_str: str | None = None) -> dict:
    """三大法人買賣超（億元）。

    證交所 2023 年把網址搬到 /rwd/zh/ 底下，舊路徑不一定還通，
    所以依序試多個來源；全部失敗會印出原因，不要默默回空值。
    """
    urls = [
        "https://www.twse.com.tw/rwd/zh/fund/BFI82U",
        "https://www.twse.com.tw/fund/BFI82U",
    ]
    params = {
        "response": "json",
        "type": "day",
        "dayDate": date_str or "",
        "weekDate": "",
        "monthDate": "",
    }

    errors = []
    for url in urls:
        try:
            r = SESSION.get(
                url, params=params, timeout=30,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            r.raise_for_status()
            j = r.json()
            if j.get("stat") != "OK" or not j.get("data"):
                errors.append(f"{url} -> {j.get('stat')}")
                continue

            fields = j.get("fields") or []

            def idx(*keys: str) -> int:
                for i, f in enumerate(fields):
                    if any(k in str(f) for k in keys):
                        return i
                return -1

            i_buy, i_sell, i_diff = idx("買進"), idx("賣出"), idx("差額", "買賣超")
            out: dict = {}
            for row in j["data"]:
                vals = [
                    _to_num(pd.Series([row[i]])).iloc[0] if i >= 0 else 0.0
                    for i in (i_buy, i_sell, i_diff)
                ]
                if pd.notna(vals[2]):
                    _accumulate_inst(
                        out, str(row[0]),
                        float(vals[0] or 0), float(vals[1] or 0), float(vals[2]),
                    )
            if out:
                return out
            errors.append(f"{url} -> 解析後為空")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url} -> {exc}")

    # 最後試 openapi（只有最新一日）
    if not date_str:
        try:
            d = fetch(BFI82U_URL)
            name_col = next((c for c in d.columns if "name" in c.lower() or "單位" in c), None)
            diff_col = next(
                (c for c in d.columns if "difference" in c.lower() or "差額" in c), None
            )
            buy_col = next((c for c in d.columns if "buy" in c.lower() or "買進" in c), None)
            sell_col = next((c for c in d.columns if "sell" in c.lower() or "賣出" in c), None)
            if name_col and diff_col:
                out = {}
                for _, r in d.iterrows():
                    def num(c):
                        return float(_to_num(pd.Series([r[c]])).iloc[0] or 0) if c else 0.0

                    diff = _to_num(pd.Series([r[diff_col]])).iloc[0]
                    if pd.notna(diff):
                        _accumulate_inst(
                            out, str(r[name_col]), num(buy_col), num(sell_col), float(diff)
                        )
                if out:
                    return out
            errors.append(f"openapi 欄位不符：{list(d.columns)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"openapi -> {exc}")

    print("  （三大法人抓取失敗，總覽將略過該項）")
    for e in errors:
        print(f"    - {e}")
    return {}


def make_summary(
    df: pd.DataFrame,
    all_quotes: pd.DataFrame,
    data_date: str,
    index_info: dict,
    inst_info: dict,
) -> list[str]:
    """用手上的資料生出一段盤勢總覽文字。"""
    lines = []

    # --- 大盤 ---
    if index_info:
        idx, chg = index_info.get("index"), index_info.get("change")
        amt = index_info.get("amount")
        tone = "大漲" if chg > 300 else "上漲" if chg > 0 else "下跌" if chg > -300 else "重挫"
        head = f"加權指數收 {idx:,.2f} 點，{tone} {abs(chg):,.2f} 點"
        if amt:
            head += f"，成交金額約 {amt / 1e8:,.0f} 億元"
        if index_info.get("otc_index") is not None:
            oc = index_info["otc_change"]
            otone = "上漲" if oc > 0 else "下跌" if oc < 0 else "平盤"
            head += f"；櫃買指數收 {index_info['otc_index']:,.2f} 點"
            head += f"，{otone} {abs(oc):,.2f} 點" if oc else "，與前日持平"
        lines.append(head + "。")

    # --- 漲跌家數（用全市場，不只族群成分股）---
    q = all_quotes.dropna(subset=["close", "change"])
    q = q[(q["close"] > 0) & (q["close"] - q["change"] > 0)]
    # 只算普通股：代號剛好 4 碼數字。
    # 權證是 6 碼、ETF 多為 5~6 碼，混進來家數會爆增到好幾千檔。
    code_s = q["code"].astype(str)
    q = q[code_s.str.fullmatch(r"\d{4}") & ~code_s.str.startswith("00")]
    if len(q):
        pct_all = q["change"] / (q["close"] - q["change"]) * 100
        up, down, flat = (pct_all > 0).sum(), (pct_all < 0).sum(), (pct_all == 0).sum()
        breadth = "價量俱揚" if up > down * 1.5 else "多空分歧" if up > down * 0.7 else "賣壓沉重"
        # 離線 CSV 只有少數幾檔，稱「全市場」會誤導
        scope = "全市場" if len(q) > 300 else "觀察範圍內"
        breadth_line = f"{scope}上漲 {up} 檔、下跌 {down} 檔、平盤 {flat} 檔，{breadth}"

        # 三大法人買賣超接在同一句後面
        if inst_info:
            parts = [
                f"{k}{'買超' if v['net'] >= 0 else '賣超'} {abs(v['net']):,.0f} 億"
                for k, v in inst_info.items()
            ]
            total = sum(v["net"] for v in inst_info.values())
            breadth_line += (
                "；三大法人" + "、".join(parts)
                + f"，合計{'買超' if total >= 0 else '賣超'} {abs(total):,.0f} 億元"
            )
        lines.append(breadth_line + "。")

    # --- 法人買進／賣出明細（金額大，另起一行才好讀）---
    if inst_info:
        detail = [
            f"{k} 買進 {v['buy']:,.0f} 億／賣出 {v['sell']:,.0f} 億／"
            f"{'買超' if v['net'] >= 0 else '賣超'} {abs(v['net']):,.0f} 億"
            for k, v in inst_info.items()
            if v["buy"] or v["sell"]
        ]
        if detail:
            lines.append("法人進出明細：" + "；".join(detail) + "。")

    # --- 族群強弱 ---
    g = df.groupby("group")["pct"].mean().sort_values(ascending=False)
    if len(g) >= 3:
        strong = "、".join(f"{k} {v:+.2f}%" for k, v in g.head(3).items())
        weak = "、".join(f"{k} {v:+.2f}%" for k, v in g.tail(3).items())
        lines.append(f"族群方面，最強為 {strong}；最弱為 {weak}。")

    # --- 漲停 / 跌停 ---
    limit_up = df[df["pct"] >= LIMIT_PCT].sort_values("pct", ascending=False)
    if len(limit_up):
        names = "、".join(f"{r['name']}({r['code']})" for _, r in limit_up.head(6).iterrows())
        more = f" 等 {len(limit_up)} 檔" if len(limit_up) > 6 else ""
        lines.append(f"觀察名單中亮燈漲停：{names}{more}。")

    limit_dn = df[df["pct"] <= -LIMIT_PCT].sort_values("pct")
    if len(limit_dn):
        names = "、".join(f"{r['name']}({r['code']})" for _, r in limit_dn.head(6).iterrows())
        lines.append(f"跌停：{names}。")

    # --- 最強最弱個股 ---
    top = df.nlargest(5, "pct")
    bot = df.nsmallest(5, "pct")
    lines.append(
        "個股強勢：" + "、".join(f"{r['name']} {r['pct']:+.2f}%" for _, r in top.iterrows()) + "。"
    )
    lines.append(
        "個股弱勢：" + "、".join(f"{r['name']} {r['pct']:+.2f}%" for _, r in bot.iterrows()) + "。"
    )

    # --- 背離提醒：整個族群逆勢 ---
    if index_info and index_info.get("change") is not None:
        mkt_up = index_info["change"] > 0
        for name, val in g.items():
            sub = df[df["group"] == name]
            if len(sub) < 3:
                continue
            if mkt_up and val < -1 and (sub["pct"] < 0).all():
                lines.append(f"值得留意：大盤上漲但 {name} 全數收黑（平均 {val:+.2f}%），呈現背離。")
            elif not mkt_up and val > 1 and (sub["pct"] > 0).all():
                lines.append(f"值得留意：大盤下跌但 {name} 全數收紅（平均 {val:+.2f}%），逆勢抗跌。")

    return lines


def make_figure(df: pd.DataFrame, subtitle: str = ""):
    fig = px.treemap(
        df,
        path=[px.Constant("台股族群"), "group", "label"],
        values="size",
        color="pct",
        color_continuous_scale=TW_COLORSCALE,
        range_color=[-COLOR_RANGE, COLOR_RANGE],
        color_continuous_midpoint=0,
        custom_data=["pct"],
        title=f"台股族群熱力圖（每檔等大、紅漲綠跌）{subtitle}",
    )
    fig.update_traces(
        # 注意：texttemplate 不支援 %{color}，漲跌幅要靠 customdata 帶進來
        texttemplate="%{label}<br>%{customdata[0]:+.2f}%",
        textposition="middle center",
        textfont=dict(size=12, color="white", family="Microsoft JhengHei, sans-serif"),
        marker=dict(line=dict(color="#111111", width=1)),
        # 只用 label / color / value，這三個在「個股」與「族群」兩層都有值，
        # 不會像 customdata 那樣在族群層變成 NaN 或 (?)
        hovertemplate=(
            "<b>%{label}</b><br>"
            "漲跌幅：%{color:+.2f}%<br>"
            "%{value:.0f} 檔<extra></extra>"
        ),
        root_color="#111111",
    )
    fig.update_layout(
        margin=dict(t=60, l=10, r=10, b=10),
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",
        font=dict(color="white", family="Microsoft JhengHei, sans-serif"),
        coloraxis_colorbar=dict(title="漲跌幅 %", ticksuffix="%"),
    )
    return fig


def print_group_summary(df: pd.DataFrame) -> None:
    """用市值加權算各族群強弱，排出當日最強／最弱族群。"""
    g = (
        df.groupby("group")
        .apply(
            lambda x: pd.Series(
                {
                    "檔數": len(x),
                    "平均漲跌%": x["pct"].mean(),
                    "市值加權漲跌%": (
                        (x["pct"] * x["market_cap"]).sum() / x["market_cap"].sum()
                        if x["market_cap"].sum() > 0
                        else x["pct"].mean()
                    ),
                }
            ),
            include_groups=False,
        )
        .sort_values("平均漲跌%", ascending=False)
    )
    print("\n族群強弱排行：")
    print(g.round(2).to_string())


def load_from_csv(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """離線模式：讀取事先存好的 CSV（欄位 group,code,name,close,change）。

    好處是網路不通、或想重畫某一天的圖時都能用；
    缺點是沒有市值資料，所以強弱排行只會有等權平均。
    """
    raw = pd.read_csv(path, dtype={"code": str})
    quotes = pd.DataFrame(
        {
            "code": raw["code"].astype(str).str.strip(),
            "name": raw["name"].astype(str).str.strip(),
            "close": _to_num(raw["close"]),
            "change": _to_num(raw["change"]),
            "value": 0.0,
            "market": "CSV",
        }
    ).drop_duplicates(subset=["code"])
    capital = pd.DataFrame({"code": [], "capital": []})
    return quotes, capital


def parse_args() -> tuple[str | None, str | None]:
    """回傳 (csv路徑, 日期YYYYMMDD)。兩者都沒有就抓最新收盤。"""
    csv_path = date_str = None
    for arg in sys.argv[1:]:
        if arg.lower().endswith(".csv"):
            csv_path = arg
        elif arg.isdigit() and len(arg) == 8:
            date_str = arg
    return csv_path, date_str


def write_html_with_summary(
    fig, path: str, data_date: str, summary: list[str], chips_data: dict | None = None
) -> None:
    """把總覽文字插在圖表上方，存成單一 HTML。"""
    import html as html_mod
    import re

    body = "".join(
        f"<p style='margin:0 0 8px 0'>{html_mod.escape(line)}</p>" for line in summary
    )
    block = (
        "<div style=\"background:#1a1a1a;color:#e8e8e8;"
        "font-family:'Microsoft JhengHei',sans-serif;padding:20px 28px;"
        "line-height:1.9;font-size:15px;border-bottom:1px solid #333\">"
        f"<h2 style='margin:0 0 14px 0;font-size:19px;color:#fff'>"
        f"今日盤勢總覽　<span style='font-size:14px;color:#888'>{data_date}</span></h2>"
        f"{body}</div>"
    )

    if chips_data and chips_mod:
        block += chips_mod.to_html(chips_data)

    doc = fig.to_html(include_plotlyjs="cdn", full_html=True)
    doc = doc.replace("<body>", "<body style='margin:0;background:#111111'>", 1)
    doc, n = re.subn(r"(<body[^>]*>)", lambda m: m.group(1) + block, doc, count=1)
    if n == 0:  # 找不到 body 就退而求其次接在最前面
        doc = block + doc

    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


def main() -> int:
    csv_path, date_str = parse_args()

    if csv_path:
        print(f"離線模式：讀取 {csv_path}")
        data_date = csv_path
        try:
            quotes, capital = load_from_csv(csv_path)
        except Exception as exc:  # noqa: BLE001
            print(f"讀取 CSV 失敗：{exc}")
            return 1
    elif date_str:
        print(f"抓取 {date_str} 的收盤資料（上市＋上櫃）…")
        try:
            quotes, data_date = load_quotes_by_date(date_str)
            capital = load_capital()
        except Exception as exc:  # noqa: BLE001
            print(f"抓取資料失敗：{exc}")
            return 1
    else:
        import datetime
        import os

        stamp_today = datetime.date.today().strftime("%Y%m%d")
        done = f"docs/archive/twstock_group_heatmap_{stamp_today}.html"
        if os.path.exists(done):
            print(f"今天的檔案已存在（{done}），不用重跑")
            return 0
        print("下載台股資料中…")
        try:
            quotes, data_date = load_quotes()
            capital = load_capital()
        except Exception as exc:  # noqa: BLE001
            print(f"抓取資料失敗：{exc}")
            print("提示：可改用離線模式  python twstock_group_heatmap.py 檔案.csv")
            return 1

    import datetime

    today = datetime.date.today().strftime("%Y/%m/%d")
    print(f"\n★ 資料日期：{data_date}")
    if not csv_path and not date_str and data_date != today:
        print(
            f"  注意：這不是今天（{today}）的資料，本次不輸出檔案。\n"
            "  可能是證交所盤後資料還沒更新，或今天休市。\n"
            "  稍晚的排程會再試一次；也可以手動指定日期補跑。"
        )
        return EXIT_STALE

    df = build_dataframe(quotes, capital)
    no_cap = int((df["market_cap"] == 0).sum())
    print(f"\n共 {len(df)} 檔，{df['group'].nunique()} 個族群")
    if no_cap:
        print(f"（其中 {no_cap} 檔查無市值資料，仍照常顯示）")
    print_group_summary(df)

    # --- 盤勢總覽 ---
    index_info = fetch_index_info(data_date) if not csv_path else {}
    if index_info and not csv_path:
        index_info.update(fetch_otc_index(data_date))
    inst_info = fetch_institutional(date_str) if not csv_path else {}
    summary = make_summary(df, quotes, data_date, index_info, inst_info)

    print("\n" + "=" * 60)
    print(f"今日盤勢總覽（{data_date}）")
    print("=" * 60)
    for line in summary:
        print("  " + line)

    # --- 籌碼資料 ---
    chips_data = None
    if chips_mod and not csv_path and "/" in data_date:
        chips_data = chips_mod.fetch_all(data_date.replace("/", ""))
        print("\n" + "=" * 60)
        print("籌碼資料")
        print("=" * 60)
        for line in chips_mod.to_text(chips_data):
            print(line)

    label = f"　資料日期：{data_date}"
    fig = make_figure(df, label)

    stamp = data_date.replace("/", "") if "/" in data_date else "latest"
    out = f"twstock_group_heatmap_{stamp}.html"
    write_html_with_summary(fig, out, data_date, summary, chips_data)
    print(f"\n已輸出：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
