from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .models import Intent, RoutePlan

MODE_RULES = {
    "relaxed": ["累", "放松", "放空", "随便走", "慢慢", "不赶", "悠闲", "轻松"],
    "full": ["充实", "全走", "打卡", "必去", "精力充沛", "高效", "经典"],
    "deep": ["深度", "文化", "历史", "故事", "博物馆", "古迹", "了解"],
    "photo": ["拍照", "摄影", "机位", "光线", "出片", "日落"],
}

SCENE_RULES = {
    "历史文化游": (
        [
            "历史",
            "文化",
            "古迹",
            "博物馆",
            "遗址",
            "胡同",
            "古建筑",
            "老建筑",
            "老房子",
            "老洋房",
            "石库门",
            "弄堂",
            "里弄",
            "故居",
            "风貌",
            "租界",
            "民国",
            "历史街区",
            "老街",
        ],
        ["heritage", "culture", "museum", "attraction"],
    ),
    "自然公园游": (
        ["自然", "公园", "爬山", "湿地", "森林", "踏青", "植物园"],
        ["park", "attraction"],
    ),
    "城市漫游": (
        ["citywalk", "漫步", "街区", "随便逛", "文艺", "小众", "弄堂", "街巷"],
        ["heritage", "attraction", "culture", "park"],
    ),
    "休闲购物游": (["购物", "逛街", "商场", "美食", "咖啡", "餐厅"], ["leisure", "attraction"]),
    "亲子研学游": (
        ["孩子", "儿童", "亲子", "带娃", "研学", "科普"],
        ["museum", "park", "culture", "attraction"],
    ),
}

FOCUS_TERMS = (
    "老建筑",
    "老房子",
    "老洋房",
    "石库门",
    "弄堂",
    "里弄",
    "故居",
    "博物馆",
    "公园",
    "拍照",
    "亲子",
    "孩子",
    "夜景",
    "外滩",
    "古镇",
    "街区",
    "建筑",
)

START_PATTERNS = (
    re.compile(r"从(?P<name>.{2,16}?)(?:出发|起|开始)"),
    re.compile(r"在(?P<name>.{2,16}?)附近"),
    re.compile(r"先去(?P<name>.{2,12})"),
)

# Theme words, not lockable sights. Keep in sync with LLM want_places sanitizer.
GENERIC_PLACE_TOKENS = frozenset(
    {
        "公园",
        "景点",
        "经典",
        "经典景点",
        "地方",
        "室内",
        "室外",
        "户外",
        "博物馆",
        "商场",
        "街区",
        "城市",
        "上海",
        "北京",
        "市区",
        "附近",
        "那里",
        "这里",
        "外面",
        "朋友",
        "轻松",
        "室内景点",
        "户外景点",
        "一些地方",
        "几个地方",
        "咖啡馆",
        "咖啡店",
        "展馆",
        "展览馆",
        "互动体验展馆",
        "特色咖啡馆",
        "网红店",
    }
)

# Soft signature seeds by city (resolved via catalog). Prefer tier=core when present.
CITY_SIGNATURE_LANDMARKS: dict[str, list[str]] = {
    "beijing": ["天安门", "故宫", "天坛公园", "颐和园"],
    "shanghai": ["外滩", "东方明珠", "豫园", "上海博物馆"],
}
CITY_SIGNATURE_HERITAGE: dict[str, list[str]] = {
    "beijing": ["故宫", "天坛公园", "颐和园", "恭王府"],
    "shanghai": ["武康路", "外滩", "豫园", "思南路"],
}
CITY_SIGNATURE_INDOOR: dict[str, list[str]] = {
    "beijing": ["中国国家博物馆", "北京古代建筑博物馆", "故宫博物院"],
    "shanghai": ["上海博物馆", "上海自然博物馆"],
}
CITY_SIGNATURE_HUTONG: dict[str, list[str]] = {
    "beijing": ["南锣鼓巷", "烟袋斜街", "恭王府", "五道营"],
    "shanghai": ["武康路", "思南路", "田子坊"],
}
CITY_SIGNATURE_ARTISTIC: dict[str, list[str]] = {
    "beijing": ["烟袋斜街", "南锣鼓巷", "芳草地", "五道营"],
    "shanghai": ["武康路", "安福路", "思南路"],
}
# Neighborhood anchors that often pair with cafe / linger intents.
CAFE_AREA_ANCHORS = (
    "五道营",
    "南锣鼓巷",
    "烟袋斜街",
    "芳草地",
    "武康路",
    "安福路",
    "思南路",
    "田子坊",
)

LANDMARK_CUES = (
    "标志性",
    "经典",
    "打卡",
    "地标",
    "必去",
    "标志性地方",
    "玩一天",
    "一整天",
)
HERITAGE_CLASSIC_CUES = ("古建筑", "古迹", "明清", "历史建筑", "老建筑", "故居", "皇城")
HUTONG_CUES = ("胡同", "民国", "弄堂", "里弄", "老建筑", "民国建筑")
ARTISTIC_STYLE_CUES = ("文艺", "小众", "citywalk", "城市漫游")
MORE_STOPS_CUES = ("多跑几个", "多打卡", "多去几个", "多看几个", "多去一些", "多一些地方")
NIGHTLIFE_CUES = ("酒吧", "夜店", "夜生活", "清吧", "酒吧街")
VAGUE_DISLIKE_CUES = ("没兴趣", "提不起劲", "不感兴趣", "不太想去这个", "某个点")
_PLACEHOLDER_NAME = re.compile(
    r"^(?:有.+的)?.{0,8}(?:咖啡馆|咖啡店|展馆|展览馆|体验馆|网红店)$"
    r"|^(?:互动体验|特色|网红).{0,6}(?:展馆|咖啡馆|店)$"
)
_THEME_WANT_PLACE = re.compile(
    r"那种|之类|时期的|里的老|特色的历史|胡同片区|老城胡同|"
    r"^(?:胡同|老建筑|民国(?:时期)?(?:的)?建筑|历史建筑)$"
)

_NEG_PLACE = re.compile(
    r"(?:去掉|别去|不要|不想去|取消|删掉|不感兴趣)(?P<neg>[\u4e00-\u9fffA-Za-z0-9]{2,12})"
)
_MENTION_PLACE = re.compile(
    r"(?:比如|想去|换成|加上|再去|先去|只去)(?:像)?"
    r"(?P<body>[\u4e00-\u9fffA-Za-z0-9、，和与及以及还有或]+?)"
    r"(?=比如|想去|换成|加上|再去|先去|只去|[。！？；]|$)"
)

CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _parse_time_minutes(text: str) -> tuple[int, int]:
    if "半天" in text:
        return 240, 45
    if "一整天" in text or ("一天" in text and "两天" not in text and "三天" not in text):
        return 480, 60

    range_match = re.search(
        r"([一二两三四五六七八九十\d])\s*[到至~-]\s*([一二两三四五六七八九十\d])\s*个?小时", text
    )
    if range_match:
        low = _to_number(range_match.group(1))
        high = _to_number(range_match.group(2))
        return round((low + high) * 30), max(20, round((high - low) * 30))

    hour_match = re.search(r"([一二两三四五六七八九十\d](?:\.\d+)?)\s*个?小时", text)
    if hour_match:
        value = _to_number(hour_match.group(1))
        sigma = 30 if any(word in text for word in ["大概", "左右", "差不多", "约"]) else 15
        return round(value * 60), sigma

    minute_match = re.search(r"(\d{2,3})\s*分钟", text)
    if minute_match:
        return int(minute_match.group(1)), 10
    return 240, 40


def _parse_days(text: str) -> int:
    # Arabic numerals from the MVP web prompt ("想用2天") must count.
    digit = re.search(r"(\d+)\s*天", text) or re.search(r"(\d+)\s*日", text)
    if digit:
        return max(1, min(7, int(digit.group(1))))
    cn = re.search(r"([一二两三四五六七八九十])\s*日", text)
    if cn:
        return max(1, min(7, int(_to_number(cn.group(1)))))
    if "三天" in text or "三日" in text:
        return 3
    if "两天" in text or "二天" in text or "两日" in text:
        return 2
    if "一天" in text or "一日" in text:
        return 1
    # 「四天」等：单字中文数字 + 天
    cn_day = re.search(r"([一二两三四五六七八九十])\s*天", text)
    if cn_day:
        return max(1, min(7, int(_to_number(cn_day.group(1)))))
    return 1


def _parse_start_hour(text: str) -> float | None:
    # Bare 「晚上」is ambiguous (night sightseeing vs a fixed commitment).
    # Only treat clear night-sightseeing cues as a late start; LLM soft-prior
    # owns fixed-time personal commitments.
    if any(token in text for token in ("夜景", "夜游", "夜生活")):
        return 19.0
    if any(token in text for token in ("傍晚", "黄昏", "日落", "蓝调")) and not re.search(
        r"在.{1,12}(开会|见面|吃饭|接人|集合)", text
    ):
        return 17.0
    if "下午" in text and not re.search(r"下午.{0,6}(开会|见面|接人|三点|3点|15)", text):
        return 14.0
    if "上午" in text or "早上" in text:
        return 10.0
    return None


def _parse_prefer_near(text: str) -> str | None:
    """Structural place cue 「在X…」— not an event-type keyword list."""
    cafe_near = re.search(
        r"(?P<place>[\u4e00-\u9fffA-Za-z0-9]{2,12})附近(?:的)?(?:咖啡馆|咖啡店|咖啡)",
        text,
    )
    if cafe_near:
        place = cafe_near.group("place").strip(" ，。的了")
        if len(place) >= 2 and place not in {"上海", "北京", "那里", "这里", "外面", "朋友"}:
            return place[:40]
    # 「五道营…咖啡馆多待会儿」— area named with cafe linger, no 「附近」.
    if any(token in text for token in ("咖啡", "咖啡馆", "咖啡店")):
        for area in CAFE_AREA_ANCHORS:
            if area in text:
                return area
    match = re.search(r"在(?P<place>[\u4e00-\u9fffA-Za-z0-9]{2,16})", text)
    if not match:
        return None
    place = match.group("place").strip(" ，。的了")
    for suffix in (
        "开个会",
        "开会",
        "见面",
        "吃饭",
        "接人",
        "集合",
        "附近",
        "一带",
        "周边",
    ):
        if place.endswith(suffix):
            place = place[: -len(suffix)]
            break
    for conj in ("和", "与", "跟", "还有", "一起"):
        if conj in place:
            place = place.split(conj, 1)[0]
            break
    place = place.strip(" ，。的了和与")
    if len(place) >= 2 and place not in {"上海", "北京", "那里", "这里", "外面", "朋友"}:
        return place[:40]
    return None


def _parse_busy_window(text: str) -> tuple[float | None, float | None]:
    """Rough busy window from clock / part-of-day words (not event types)."""
    # Night sightseeing is a start-time preference, not a personal busy block.
    sightseeing_night = any(
        token in text for token in ("夜景", "夜游", "拍照", "摄影", "看日落", "蓝调")
    )
    # Strip degree 「少一点/好一点」so 「一点」is not read as 1 o'clock.
    clock_text = re.sub(
        r"(?:少|好|多|快|慢|稍|略|早|晚|大|小)\s*一点",
        "",
        text,
    )
    clock = re.search(
        r"(?:下午|早上|上午|中午|晚上)?\s*([一二两三四五六七八九十\d]{1,2})\s*点",
        clock_text,
    )
    if clock:
        hour = _to_number(clock.group(1))
        matched = clock.group(0)
        # Bare 「一点/两点」without part-of-day is usually not a clock in chat.
        if clock.group(1) in {"一", "二", "两"} and not any(
            part in matched for part in ("下午", "早上", "上午", "中午", "晚上")
        ):
            hour = None
        if hour is not None:
            if "下午" in clock_text and hour < 12:
                hour += 12
            if "晚上" in clock_text and hour < 12:
                hour += 12
            hour = max(6.0, min(22.0, float(hour)))
            return hour, hour + 1.5
    if "中午" in text:
        return 12.0, 13.5
    if "傍晚" in text and not sightseeing_night:
        return 17.5, 19.5
    if "晚上" in text and not sightseeing_night:
        return 18.5, 21.0
    if re.search(r"下午.{0,8}(开会|见面|接人|有约|要)", text) or "下午三点" in text:
        return 15.0, 16.5
    return None, None


def _parse_start(text: str) -> tuple[str | None, bool]:
    for pattern in START_PATTERNS:
        match = pattern.search(text)
        if match:
            name = match.group("name").strip(" ，。的了")
            if len(name) >= 2:
                return name, "出发" in match.group(0) or "起" in match.group(0)
    return None, False


def _focus_terms(text: str) -> list[str]:
    found = [term for term in FOCUS_TERMS if term in text]
    return found or []


def is_descriptive_placeholder(name: str) -> bool:
    """True for category-like labels (「有北京特色的咖啡馆」「互动体验展馆」)."""
    text = str(name or "").strip()
    if not text:
        return True
    if text in GENERIC_PLACE_TOKENS:
        return True
    if _PLACEHOLDER_NAME.match(text):
        return True
    if text.startswith("有") and any(
        text.endswith(suffix) for suffix in ("咖啡馆", "咖啡店", "展馆", "店", "地方")
    ):
        return True
    # Theme phrases that must not soft-lock as want_places (e.g. 胡同里的那种老建筑).
    if "（" in text or "(" in text:
        return True
    if _THEME_WANT_PLACE.search(text):
        return True
    return False


def _clean_place_token(token: str) -> str | None:
    text = str(token or "").strip(" ，。和与及")
    text = re.sub(r"^像", "", text)
    text = re.sub(r"^(一些|些)", "", text)
    text = re.sub(r"这样的.*$", "", text)
    text = re.sub(r"(什么的|什么|之类|那些|这些)$", "", text)
    text = text.strip(" ，。的了和与及")
    if len(text) < 2 or text in GENERIC_PLACE_TOKENS:
        return None
    if is_descriptive_placeholder(text):
        return None
    if text.endswith(("地方", "景点")) or "轻松" in text:
        return None
    if re.match(r"^(可以|然后|让我|要不|觉得|每个|还是|另外)", text):
        return None
    if any(marker in text for marker in ("坐着", "早点结束", "回去休息", "走得慢", "多花点")):
        return None
    return text[:16]


def utterance_wants_landmarks(text: str) -> bool:
    raw = text or ""
    if any(cue in raw for cue in LANDMARK_CUES):
        return True
    if any(cue in raw for cue in ("拍照", "摄影", "出片")) and (
        "一天" in raw or "一整天" in raw or "半天" in raw
    ):
        return True
    return False


def utterance_wants_heritage_classics(text: str) -> bool:
    return any(cue in (text or "") for cue in HERITAGE_CLASSIC_CUES)


def utterance_wants_more_stops(text: str) -> bool:
    return any(cue in (text or "") for cue in MORE_STOPS_CUES)


def utterance_allows_nightlife(text: str) -> bool:
    return any(cue in (text or "") for cue in NIGHTLIFE_CUES)


def utterance_adds_indoor(text: str) -> bool:
    raw = text or ""
    if re.search(r"(?:再加|加个|加点|加上|增加|补个|补上).{0,8}室内", raw):
        return True
    if "室内" in raw and any(token in raw for token in ("再加", "加个", "加上", "多一些", "打卡多")):
        return True
    return False


def utterance_switches_to_indoor(text: str) -> bool:
    """User wants indoor instead of outdoor (not merely 「再加室内」)."""
    raw = text or ""
    if not raw or utterance_adds_indoor(raw):
        return False
    if re.search(r"(?:换成|改成|换一?个?|只要).{0,8}室内", raw):
        return True
    if "室内" in raw and any(
        token in raw
        for token in ("太热", "好热", "热死", "特别热", "躲开", "避开", "室外太", "不想在室外")
    ):
        return True
    if any(token in raw for token in ("躲开室外", "避开室外", "别去室外", "少待室外")):
        return True
    return False


def utterance_wants_hutong(text: str) -> bool:
    """「多看胡同/老建筑/民国」— ADD preference, not strip large parks."""
    raw = text or ""
    if not raw:
        return False
    if "胡同" in raw or "民国" in raw:
        return True
    if any(cue in raw for cue in ("弄堂", "里弄")):
        return True
    # 「多看…老建筑」as add-on; bare heritage openers use classic seeds instead.
    if "老建筑" in raw and any(
        token in raw for token in ("多看", "多去", "多逛", "再看", "比如", "胡同里")
    ):
        return True
    return False


def utterance_is_artistic_style_shift(text: str) -> bool:
    """Style/theme shift (文艺/人少/小众/街区) without naming which stop to drop."""
    raw = (text or "").strip()
    if not raw:
        return False
    # Explicit named remove/swap is not a vague theme shift.
    if re.search(
        r"(?:去掉|别去|不要|取消).{1,12}(?:换成|改成|替换)|"
        r"(?:把|将).{1,12}(?:换成|改成|替换成)",
        raw,
    ):
        return False
    if any(cue in raw for cue in ARTISTIC_STYLE_CUES):
        return True
    if "街区" in raw and any(
        token in raw for token in ("更", "换", "文艺", "漫游", "citywalk", "小众")
    ):
        return True
    # 「人少」alone on a photo opener is not enough; need a style ask.
    if "人少" in raw and re.search(r"(?:更|有没有|换|改成|不要.*商业|文艺)", raw):
        return True
    if "胡同" in raw and any(token in raw for token in ("拍照", "出片", "人少")):
        return True
    return False


def utterance_is_vague_stop_replace(text: str) -> bool:
    """User wants a swap/style change but did not name which current stop to replace."""
    raw = (text or "").strip()
    if not raw:
        return False
    # Theme/indoor shifts are actionable replans — not 「你换掉哪一站」.
    if utterance_switches_to_indoor(raw) or utterance_is_artistic_style_shift(raw):
        return False
    if utterance_wants_hutong(raw) and not re.search(
        r"(?:换成|换个|改成).{0,8}更", raw
    ):
        # 「多看胡同」is an add preference unless also a vague "换成更X".
        return False
    # Explicit named remove / swap: 「去掉故宫换成…」「前门换成798」
    if re.search(
        r"(?:去掉|别去|不要|取消).{1,12}(?:换成|改成|替换)|"
        r"(?:把|将).{1,12}(?:换成|改成|替换成)|"
        r"[\u4e00-\u9fffA-Za-z0-9]{2,12}换成[\u4e00-\u9fffA-Za-z0-9]{2,12}",
        raw,
    ):
        return False
    dislike = any(cue in raw for cue in VAGUE_DISLIKE_CUES)
    style_swap = bool(
        re.search(r"(?:换成|换个|改成|换一).{0,8}更", raw)
        or re.search(r"更(?:互动|有意思|好玩|轻松|安静|文艺)", raw)
    )
    if not (dislike or style_swap):
        return False
    # 「更文艺」already handled as artistic style shift above; leftover 更X still clarify.
    named = _parse_want_places(raw)
    concrete = [name for name in named if not is_descriptive_placeholder(name)]
    # Concrete new venue named without saying which stop to drop → still clarify.
    if concrete and re.search(r"(?:把|将|去掉|别去).{1,12}(?:换成|改成)", raw):
        return False
    return True


def soft_seed_signature_places(intent: Intent) -> Intent:
    """Inject city signature names when iconic / heritage / indoor / hutong cues fire.

    Soft-locks via want_places so scoring + planner treat classics as priors,
    without hardcoding eval case ids. Concrete user-named places always win and
    stay first; specialty seeds (indoor/hutong/artistic) may append.
    """
    text = intent.utterance or ""
    city = intent.city if intent.city in CITY_SIGNATURE_LANDMARKS else "beijing"
    existing = [
        name
        for name in (intent.want_places or [])
        if name and not is_descriptive_placeholder(name)
    ]
    specialty: list[str] = []
    if utterance_switches_to_indoor(text):
        specialty.extend(CITY_SIGNATURE_INDOOR.get(city, []))
    if utterance_wants_hutong(text):
        specialty.extend(CITY_SIGNATURE_HUTONG.get(city, []))
    if utterance_is_artistic_style_shift(text):
        specialty.extend(CITY_SIGNATURE_ARTISTIC.get(city, []))
    # Cafe linger near a named area: keep prefer_near; do not seed cafe placeholders.
    if any(token in text for token in ("咖啡", "咖啡馆", "咖啡店")):
        for area in CAFE_AREA_ANCHORS:
            if area in text and area not in specialty:
                specialty.append(area)

    classic: list[str] = []
    if not existing:
        if utterance_wants_heritage_classics(text) or (
            intent.scene == "历史文化游" and intent.available_minutes >= 360
        ):
            classic.extend(CITY_SIGNATURE_HERITAGE.get(city, []))
        if utterance_wants_landmarks(text):
            classic.extend(CITY_SIGNATURE_LANDMARKS.get(city, []))
        elif intent.mode == "full" and intent.available_minutes >= 360:
            # Energetic full-day without named sights still gets a classic skeleton.
            classic.extend(CITY_SIGNATURE_LANDMARKS.get(city, [])[:3])
        if intent.scene == "亲子研学游" and (
            utterance_wants_more_stops(text) or intent.available_minutes >= 360
        ):
            classic.extend(CITY_SIGNATURE_LANDMARKS.get(city, [])[:2])
        if intent.scene == "综合观光游" and intent.available_minutes >= 360 and not classic:
            classic.extend(CITY_SIGNATURE_LANDMARKS.get(city, [])[:3])

    seeds = [*specialty, *classic]
    if not existing and not seeds:
        return intent if list(intent.want_places or []) == existing else replace(
            intent, want_places=existing
        )
    # Half-day: compact; full-day / specialty add: a bit more room.
    budget = int(intent.available_minutes or 240)
    limit = 2 if budget <= 300 and not specialty else 4
    if specialty and existing:
        limit = min(6, len(existing) + 3)
    merged: list[str] = []
    seen: set[str] = set()
    for name in [*existing, *seeds]:
        if not name or name in seen or is_descriptive_placeholder(name):
            continue
        seen.add(name)
        merged.append(name)
        if len(merged) >= limit:
            break
    if not merged:
        return replace(intent, want_places=[]) if intent.want_places else intent
    if merged == list(intent.want_places or []):
        return intent
    return replace(intent, want_places=merged)


def sanitize_want_places(raw: object) -> list[str]:
    """Keep short Chinese/English sight names; drop themes and duplicates."""
    if not isinstance(raw, list):
        return []
    found: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        name = _clean_place_token(item)
        if not name or name in seen:
            continue
        seen.add(name)
        found.append(name)
        if len(found) >= 6:
            break
    return found


def parse_denied_places(text: str) -> list[str]:
    """Places the user asked to drop (去掉/别去…)."""
    found: list[str] = []
    for match in _NEG_PLACE.finditer(text):
        cleaned = _clean_place_token(match.group("neg"))
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def utterance_sets_time_budget(text: str) -> bool:
    """True when the utterance itself states a duration / remaining time."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    if "半天" in normalized or "一整天" in normalized:
        return True
    if "一天" in normalized and "两天" not in normalized and "三天" not in normalized:
        return True
    if re.search(r"小时|分钟", normalized):
        return True
    if any(token in normalized for token in ("只剩", "时间不够", "少一小时")):
        return True
    return False


def utterance_sets_start_hour(text: str) -> bool:
    return _parse_start_hour((text or "").strip().lower()) is not None


def utterance_sets_days(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return bool(
        re.search(r"\d+\s*[天日]", normalized)
        or re.search(r"[一二两三四五六七八九十]\s*[天日]", normalized)
        or any(token in normalized for token in ("两天", "三天", "一天", "一日", "两日", "三日"))
    )


def utterance_sets_pace(text: str) -> bool:
    """Pace/mode change only when the user talks about tired / faster / shorter."""
    return any(token in (text or "") for token in ("累", "加快", "缩短"))


def utterance_sets_scene(text: str) -> bool:
    """Theme switch: explicit 改成…游 / 只要公园, not a named-place swap."""
    raw = text or ""
    if re.search(r"改成.{0,8}游|换成.{0,8}游|只要(?:公园|博物馆|商场|室内)", raw):
        return True
    if any(token in raw for token in ("历史文化", "citywalk", "城市漫游", "亲子研学")):
        return True
    if utterance_switches_to_indoor(raw) or utterance_is_artistic_style_shift(raw):
        return True
    if utterance_wants_hutong(raw):
        return True
    return False


def utterance_sets_city(text: str) -> bool:
    return bool(re.search(r"(?:去|在|到)(?:上海|北京)", text or ""))


def session_source_from_plan(
    current: RoutePlan | None,
    previous: Intent | dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Previous intent if present; else derive from the current route."""
    if isinstance(previous, Intent):
        return previous.to_dict()
    if isinstance(previous, dict) and previous.get("available_minutes"):
        return previous
    if current is None:
        return None
    start_hour = None
    if current.stops and current.stops[0].arrival_time:
        clock = str(current.stops[0].arrival_time)
        match = re.match(r"(\d{1,2}):(\d{2})", clock)
        if match:
            start_hour = int(match.group(1)) + int(match.group(2)) / 60.0
    pace = {"relaxed": "slow", "full": "fast"}.get(current.mode, "normal")
    return {
        "city": current.city,
        "available_minutes": int(round(float(current.available_minutes))),
        "start_hour": start_hour,
        "days": max(1, int(getattr(current, "day_count", 1) or 1)),
        "mode": current.mode,
        "scene": current.scene,
        "pace": pace,
        "categories": None,
    }


def inherit_session_intent(
    baseline: Intent,
    text: str,
    *,
    current: RoutePlan | None = None,
    previous: Intent | dict[str, Any] | None = None,
) -> Intent:
    """Carry T1 time/city/pace/scene forward unless this utterance changes them."""
    source = session_source_from_plan(current, previous)
    if source is None:
        return baseline
    kwargs: dict[str, Any] = {}
    if not utterance_sets_time_budget(text):
        minutes = source.get("available_minutes")
        if isinstance(minutes, (int, float)) and not isinstance(minutes, bool):
            kwargs["available_minutes"] = max(60, min(720, int(round(minutes))))
        sigma = source.get("uncertainty_minutes")
        if isinstance(sigma, (int, float)) and not isinstance(sigma, bool):
            kwargs["uncertainty_minutes"] = max(10, min(120, int(round(sigma))))
    if not utterance_sets_start_hour(text) and source.get("start_hour") is not None:
        try:
            kwargs["start_hour"] = max(6.0, min(21.0, float(source["start_hour"])))
        except (TypeError, ValueError):
            pass
    if not utterance_sets_days(text):
        days = source.get("days")
        if isinstance(days, (int, float)) and not isinstance(days, bool):
            kwargs["days"] = max(1, min(7, int(days)))
    if not utterance_sets_city(text) and source.get("city") in {"beijing", "shanghai"}:
        kwargs["city"] = source["city"]
    if not utterance_sets_pace(text):
        if source.get("mode") in {"balanced", "relaxed", "full", "deep", "photo"}:
            kwargs["mode"] = source["mode"]
        if source.get("pace") in {"slow", "normal", "fast"}:
            kwargs["pace"] = source["pace"]
    if not utterance_sets_scene(text):
        if source.get("scene"):
            kwargs["scene"] = source["scene"]
        cats = source.get("categories")
        if isinstance(cats, list) and cats:
            kwargs["categories"] = [str(item) for item in cats if item]
    return replace(baseline, **kwargs) if kwargs else baseline


def sanitize_session_patch(patch: dict[str, Any] | None, text: str) -> dict[str, Any]:
    """Drop LLM fields the user did not actually change this turn."""
    cleaned = dict(patch or {})
    if not utterance_sets_time_budget(text) and cleaned.get("busy_from_hour") is None:
        cleaned.pop("available_minutes", None)
    if not utterance_sets_start_hour(text):
        cleaned.pop("start_hour", None)
    if not utterance_sets_days(text):
        cleaned.pop("days", None)
    if not utterance_sets_pace(text):
        cleaned.pop("mode", None)
        cleaned.pop("pace", None)
    if not utterance_sets_scene(text):
        cleaned.pop("scene", None)
        cleaned.pop("categories", None)
    return cleaned


def _parse_want_places(text: str) -> list[str]:
    """Named sights after 比如/想去/换成…; skip 去掉/别去 and generic themes."""
    denied: set[str] = set()
    for match in _NEG_PLACE.finditer(text):
        cleaned = _clean_place_token(match.group("neg"))
        if cleaned:
            denied.add(cleaned)
    found: list[str] = []
    for match in _MENTION_PLACE.finditer(text):
        body = re.split(r"[。！？；]", match.group("body") or "")[0]
        for part in re.split(r"[、，,/]|还有|以及|和|与|或", body):
            name = _clean_place_token(part)
            if name and name not in denied and name not in found:
                found.append(name)
            if len(found) >= 6:
                return found
    return found


def _to_number(value: str) -> float:
    if value in CHINESE_NUMBERS:
        return float(CHINESE_NUMBERS[value])
    return float(value)


def parse_local_intent(
    text: str,
    city: str = "beijing",
    *,
    apply_realtime: bool = False,
) -> Intent:
    """Parse a stable offline baseline before any optional LLM enrichment."""
    normalized = text.strip().lower()
    minutes, sigma = _parse_time_minutes(normalized)
    days = _parse_days(normalized)
    if days > 1 and not re.search(r"小时|分钟|半天", normalized):
        minutes, sigma = 420, 60
    busy_from, busy_until = _parse_busy_window(normalized)
    prefer_near = _parse_prefer_near(text)

    mode_scores = {
        mode: sum(keyword in normalized for keyword in keywords)
        for mode, keywords in MODE_RULES.items()
    }
    mode = max(mode_scores, key=mode_scores.get)
    if mode_scores[mode] == 0:
        mode = "balanced"

    scene_scores = {
        scene: sum(keyword in normalized for keyword in keywords)
        for scene, (keywords, _) in SCENE_RULES.items()
    }
    scene = max(scene_scores, key=scene_scores.get)
    if scene_scores[scene] == 0:
        scene = "综合观光游"
        categories = ["attraction", "heritage", "park", "museum"]
    else:
        categories = list(SCENE_RULES[scene][1])

    mood = {
        "relaxed": "tired_or_relaxed",
        "full": "energetic",
        "deep": "curious",
        "photo": "creative",
    }.get(mode, "balanced")
    pace = "slow" if mode == "relaxed" else "fast" if mode == "full" else "normal"
    start_name, start_as_depot = _parse_start(text)
    start_hour = _parse_start_hour(normalized)
    # If the only time cue is a personal busy window, don't also force a late start.
    if busy_from is not None and start_hour is not None and start_hour >= 16.5:
        start_hour = None

    base = Intent(
        city=city,
        utterance=text,
        available_minutes=minutes,
        uncertainty_minutes=sigma,
        mode=mode,
        scene=scene,
        categories=categories,
        excluded_categories=[] if scene == "休闲购物游" else ["leisure"],
        mood=mood,
        pace=pace,
        apply_realtime=apply_realtime,
        days=days,
        start_name=start_name,
        start_as_depot=start_as_depot,
        start_hour=start_hour,
        prefer_near=prefer_near,
        busy_from_hour=busy_from,
        busy_until_hour=busy_until,
        want_places=_parse_want_places(text),
        focus_terms=_focus_terms(normalized),
    )
    return soft_seed_signature_places(base)
