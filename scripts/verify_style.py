#!/usr/bin/env python3
"""gn-voice 런타임 자체검증 (stdlib only — venv·형태소분석기 불필요).

사용: python3 verify_style.py <final.md> <fingerprint-slim.json> <genre> [channel]
출력: JSON 한 줄 {pass, genre, violations: [...], metrics: {...}}
- must_zero 위반 = 즉시 fail
- 범위 지표: p10~p90 ±15% 여유(reg_*는 ±0.10 절대 여유 — 정규식 근사 오차)
- n<10 장르(chat)는 direction만 참고, 범위 fail 없음
- AI 시그니처 역탐지(상투구) 포함
"""
import json
import re
import sys
from pathlib import Path


def _load_tells() -> dict:
    path = Path(__file__).resolve().parent.parent / "references" / "ai-tells.json"
    return json.loads(path.read_text(encoding="utf-8"))


def body_of(text: str) -> str:
    text = re.sub(r"<!--\s*GNVOICE-SUMMARY.*?-->", "", text, flags=re.S)
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2]
    return text


def sentences(body: str):
    raw = re.split(r"(?<=[.!?…])\s+|\n+", body)
    return [s.strip() for s in raw if len(s.strip()) > 3]


def _tell_count(sents, group: dict) -> int:
    variants = group.get("variants", [])
    if group.get("match") == "contain":
        n = sum(1 for s in sents if any(v in s for v in variants))
    else:
        n = sum(1 for s in sents if any(s.startswith(v) for v in variants))
    if group.get("series"):  # 열거 시퀀스(첫째·둘째…)는 존재 여부만 — 열거 길이를 처벌하지 않음
        return 1 if n > 0 else 0
    return n


RANGE_ALLOW = 2
SMOOTHNESS_SLACK = 1.2  # CV winner q5_95-range2-sm1p2: sm_pct = 0.10 * slack


class InputError(Exception):
    pass


# 음슴체 근사: 문장 끝 어절이 ㅁ-받침 종성(~음/~함/~감/~뺌 …)이면 카운트.
# 음절 열거 대신 종성 판정 — 코퍼스 실측상 감/뺌/줌 등 변형이 많아 열거는 과소계상.
EUMSEUM_STRIP_RE = re.compile(r'[)"\'”….?!\s~;ㄱ-ㅎㅏ-ㅣ]+$')  # 문장부호·ㅋㅋ류 자모 꼬리 제거
# 명사 오탐 블록리스트 (문장 끝에 와도 음슴체가 아닌 고빈도 일반 명사)
EUMSEUM_BLOCK = ("처음", "다음", "마음", "걸음", "얼음", "죽음", "사람", "이름",
                 "아침", "점심", "기쁨", "슬픔", "게임", "시스템", "프로그램", "아이템")


def _eumseum_end(end_word: str) -> bool:
    w = EUMSEUM_STRIP_RE.sub("", end_word)
    if not w or any(w.endswith(b) for b in EUMSEUM_BLOCK):
        return False
    o = ord(w[-1])
    return 0xAC00 <= o <= 0xD7A3 and (o - 0xAC00) % 28 == 16  # 종성 ㅁ


def compute(body: str) -> dict:
    sents = sentences(body)
    n_chars = max(len(body), 1)
    words = body.split()
    n_words = max(len(words), 1)
    k = 1000.0
    ends = [re.sub(r'[)"\'”….?!\s]+$', "", s) for s in sents]
    haeyo = sum(1 for e in ends if re.search(r"(요|죠)$", e))
    handa = sum(1 for e in ends if re.search(r"(다|까|랴|자|네|지)$", e))
    eumseum = sum(1 for e in ends if _eumseum_end(e))
    q = sum(1 for s in sents if s.rstrip().endswith("?") or "?" in s[-3:])
    fp_na = len(re.findall(r"(?<![가-힣])(나는|나도|내가|나를|나의|나에게|나한테)", body))
    fp_jeo = len(re.findall(r"(?<![가-힣])(저는|저도|제가|저를|저의|저에게|저한테)", body))
    return {
        "sent_len_mean": round(sum(len(s.split()) for s in sents) / max(len(sents), 1), 2),
        "reg_haeyo_ratio": round(haeyo / max(len(sents), 1), 3),
        "reg_haeche_handa_ratio": round(handa / max(len(sents), 1), 3),
        "reg_eumseum_ratio": round(eumseum / max(len(sents), 1), 3),
        "sent_end_question_ratio": round(q / max(len(sents), 1), 3),
        "punct_period_per1k": round((body.count(".") - len(re.findall(r"\.{2,}", body)) * 2) / n_chars * k, 2),
        "punct_exclaim_per1k": round(body.count("!") / n_chars * k, 2),
        "punct_ellipsis_informal_per1k": round(len(re.findall(r"\.{2,}|…", body)) / n_chars * k, 2),
        "punct_quote_per1k": round(len(re.findall(r'["“”]', body)) / 2 / n_chars * k, 2),
        "fp_na_per1k": round(fp_na / n_words * k, 2),
        "fp_jeo_per1k": round(fp_jeo / n_words * k, 2),
        "dm_사실_per1k": round(len(re.findall(r"(?<![가-힣])사실", body)) / n_words * k, 2),
    }


def smoothness(body: str) -> dict:
    import math
    import statistics
    from collections import Counter
    sents = sentences(body)
    if len(sents) < 5:
        return {}
    ends = [re.sub(r'[)"\'”….?!\s]+$', "", s)[-2:] for s in sents]
    c = Counter(ends)
    n = len(ends)
    entropy = -sum((v / n) * math.log2(v / n) for v in c.values())
    lens = [len(s.split()) for s in sents]
    out = {"ending_entropy": round(entropy, 3),
           "sent_len_cv": round(statistics.pstdev(lens) / max(statistics.mean(lens), 1e-9), 3)}
    lines = [l for l in body.splitlines() if l.strip()]
    if len(lines) > 3:
        lls = [len(l) for l in lines]
        out["line_len_cv"] = round(statistics.pstdev(lls) / max(statistics.mean(lls), 1e-9), 3)
    return out


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise InputError(f"{path}: {e}") from e
    except UnicodeDecodeError as e:
        raise InputError(f"{path}: {e}") from e


def _read_json(path: str) -> dict:
    try:
        return json.loads(_read_text(path))
    except json.JSONDecodeError as e:
        raise InputError(f"{path}: JSON parse failed: {e}") from e


def main() -> int:
    if len(sys.argv) < 4:
        raise InputError("usage: verify_style.py <final.md> <fingerprint-slim.json> <genre> [channel]")
    final_path, slim_path, genre = sys.argv[1], sys.argv[2], sys.argv[3]
    body = body_of(_read_text(final_path))
    slim = _read_json(slim_path)
    genres = slim.get("genres")
    if not isinstance(genres, dict):
        raise InputError(f"{slim_path}: missing genres object")
    genre_key = genre if not genre.startswith("wm-") else "column"
    if genre_key not in genres:
        raise InputError(f"{slim_path}: genre cell not found: {genre_key}")
    metrics = compute(body)
    violations = []
    # 0. 매끈함(윤문티) 하한 검사 — cycle-1 P0: 값이 실제 코퍼스 p10 미만이면 "너무 매끈"
    sm = smoothness(body)
    metrics.update(sm)
    sm_spec = genres[genre_key].get("smoothness", {})
    for k, spec in sm_spec.items():
        v = sm.get(k)
        if v is None:
            continue
        sm_pct = 0.10 * SMOOTHNESS_SLACK
        lo = spec["p10"] * (1.0 - sm_pct)
        hi = spec["p90"] * (1.0 + sm_pct)
        if v < lo or v > hi:
            violations.append({"metric": k, "type": "smoothness_band", "value": v,
                               "expected": f"[{spec['p10']}, {spec['p90']}] slack={sm_pct:.2f}"})
    # 1. must_zero
    # 초성체는 답글(reply) 전용 자산 — 코퍼스 실측 kh 6.10/1k, 그 외 전 셀 0.000
    if genre != "reply" and re.search(r"[ㅋㅎ]{2,}", body):
        violations.append({"metric": "kh_density", "type": "must_zero", "msg": "ㅋㅋ/ㅎㅎ 초성체 발견 (reply 외 금지)"})
    if re.search(r"\*\*|^#{1,6}\s", body, re.M):
        violations.append({"metric": "markdown", "type": "must_zero", "msg": "볼드/헤딩 발견"})
    if re.search(r"[\U0001F300-\U0001FAFF✨✅\U0001F525\U0001F4A1]", body):
        violations.append({"metric": "emoji", "type": "must_zero", "msg": "이모지 발견"})
    # 2. AI 상투구 역탐지
    ai_tells = _load_tells()
    sents = sentences(body)
    hard = set(ai_tells.get("hard", []))
    capped = ai_tells.get("capped", {})
    group_counts = {
        group["canon"]: _tell_count(sents, group)
        for group in ai_tells.get("groups", [])
    }
    for canon, count in group_counts.items():
        if canon in hard and count > 0:
            violations.append({"metric": "ai_tell", "type": "forbidden_phrase", "msg": canon})
    capped_counts = {canon: group_counts.get(canon, 0) for canon in capped}
    for canon, count in capped_counts.items():
        cap = capped[canon]
        if count > cap:
            violations.append({"metric": "ai_tell", "type": "tell_cap", "msg": f"{canon}×{count}(상한 {cap})"})
    capped_kinds = sum(1 for count in capped_counts.values() if count > 0)
    if capped_kinds > ai_tells.get("combo_limit", 2):
        violations.append({"metric": "ai_tell", "type": "tell_combo", "msg": f"상투구 {capped_kinds}종 동반"})
    # 3. 범위 검사
    cell = genres[genre_key]
    mode = cell.get("range_mode", "direction_only")
    if mode == "p10p90" and not genre.startswith("wm-"):
        for m, spec in cell.get("metrics", {}).items():
            if "p10" not in spec:
                continue
            v = metrics.get(m)
            lo, hi = spec["p10"], spec["p90"]
            slack = 0.10 if m.startswith("reg_") else max(abs(hi - lo) * 0.15, 0.3)
            if v is not None and (v < lo - slack or v > hi + slack):
                violations.append({"metric": m, "type": "range", "value": v,
                                   "expected": f"[{lo}, {hi}]±{round(slack, 2)}"})
    hard_fail = any(x["type"] in ("must_zero", "forbidden_phrase", "tell_cap", "tell_combo") for x in violations)
    smooth_fails = [x for x in violations if x["type"] == "smoothness_band"]
    range_fails = [x for x in violations if x["type"] == "range"]
    # cycle-3: smoothness_band는 blocking — 과매끈이 곧 위장 실패의 최대 신호
    verdict = {"pass": not hard_fail and not smooth_fails and len(range_fails) <= RANGE_ALLOW, "genre": genre,
               "range_mode": mode, "violations": violations, "metrics": metrics}
    print(json.dumps(verdict, ensure_ascii=False))
    return 0 if verdict["pass"] else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InputError as e:
        print(f"verify_style.py: {e}", file=sys.stderr)
        sys.exit(2)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"verify_style.py: {e}", file=sys.stderr)
        sys.exit(2)
