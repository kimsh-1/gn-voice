#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fingerprint.py — 빌드타임 정량 문체 분석 스크립트 (gn_is_not_ai)

목적
----
저자(train) 코퍼스와 대조군(reference) 코퍼스의 문체 지표를 kiwipiepy 형태소분석
기반으로 문서 단위로 산출하고, (source,genre)/genre/author_pooled/reply 셀로 집계하여
analysis/quant/fingerprint-{gn,ref}.json 을 생성한다. --calibrate 로 두 json을 읽어
저자 셀별 지표가 대조군 평균·표준편차 대비 몇 시그마인지 z-프로필을 산출해
style-profile/fingerprint/zprofile.json 에 기록한다.

실행은 반드시 ~/.venvs/gn/bin/python 으로 (kiwipiepy 의존, venv는 ext4 위에 있음):
    ~/.venvs/gn/bin/python scripts/fingerprint.py --corpus gn
    ~/.venvs/gn/bin/python scripts/fingerprint.py --corpus ref
    ~/.venvs/gn/bin/python scripts/fingerprint.py --calibrate

topic 오염 방지
----------------
명사(NNG/NNP) 내용어의 "형태"(어휘 자체)는 어디에도 집계하지 않는다. 사용하는 것은
품사 비율/태그열/특정 기능어(조사·어미) 형태뿐이다. 유일한 예외는 1인칭 자기지시
표지로 명시 요청된 '필자'(NNG) — 이는 개별 어휘를 모으는 것이 아니라 자기지시 여부만
카운트하는 것이라 topic 오염과 무관하다.

집계 셀
--------
- author_pooled       : gn train 전체 (threads-reply 포함, 채널별 줄바꿈 리듬은 채널 분리)
- reply               : threads-reply만 (장르 풀에 절대 섞지 않음 — genre=None이라 구조적으로도 분리됨)
- source_genre:S/G    : (source, genre) 조합별
- genre:G             : genre별 (threads+brunch 통합, reply는 genre=None이라 자동 제외)
- ref 모드: type:general/speech/expository/argumentative + ref_pooled(전체)

각 셀은 문서 단위 산출값의 mean/std/p10/p50/p90 를 기록한다(분포 보존, 대표값 하나로 뭉개지 않음).
줄바꿈 리듬만은 채널(threads/brunch) 태그를 필수로 유지한다 — 혼합 셀은
"line_rhythm_by_channel"로 채널별 서브블록을 별도로 기록한다.

n<10인 셀은 calibrate 단계에서 z 수치(range)를 금지하고 "direction"(+/-/0)만 남긴다.
"""

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    from kiwipiepy import Kiwi
except ImportError:
    sys.exit(
        "kiwipiepy 미설치 환경입니다. 반드시 ~/.venvs/gn/bin/python 으로 실행하십시오.\n"
        "  ~/.venvs/gn/bin/python scripts/fingerprint.py --corpus gn"
    )

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
ANALYSIS_DIR = ROOT / "analysis" / "quant"
ZPROFILE_DIR = ROOT / "style-profile" / "fingerprint"

EXCLUDE_IDS = {"B-322"}  # 인용 프롬프트 오염 — manifest note: "인용 프롬프트 블록은 fingerprint 제외"

SOURCE_DIR = {
    "threads": CORPUS / "threads",
    "threads-reply": CORPUS / "threads-replies",
    "brunch": CORPUS / "brunch" / "clean",
    "threads-v4": CORPUS / "threads-v4",        # v4 원글 — 셀 집계 시 threads 그룹
    "threads-v4-reply": CORPUS / "threads-v4",  # v4 본인답글 — 셀 집계 시 threads-reply 그룹
}
# 셀 집계용 소스 그룹 정규화: v4 표본이 기존 셀(threads/reply)에 합류하도록
SOURCE_GROUP = {"threads-v4": "threads", "threads-v4-reply": "threads-reply"}

REF_TYPES = ["general", "speech", "expository", "argumentative"]

DISCOURSE_MARKERS = [
    "그리고", "하지만", "그런데", "근데", "다만", "사실",
    "결국", "아무튼", "그래서", "즉", "또한", "물론",
]

J_TAGS = ["JKS", "JKC", "JKG", "JKO", "JKB", "JKV", "JKQ", "JX", "JC"]
E_FAMILY = {"EP", "EC", "EF", "ETN", "ETM"}
NOMINAL_END_TAGS = {"NNG", "NNP", "NNB", "NR", "NP", "XSN"}
# 문장 끝 레지스터 판정 시 걷어낼 비어휘 토큰(문장부호·기호·웹 요소).
# 이걸 안 걷어내면 "~했음." 의 마지막 토큰이 SF(마침표)로 읽혀 nominal 탐지가 전멸한다.
SYMBOL_SKIP_TAGS = {
    "SF", "SP", "SS", "SSO", "SSC", "SE", "SO", "SW", "SB",
    "W_URL", "W_EMAIL", "W_HASHTAG", "W_MENTION", "W_SERIAL", "W_EMOJI",
}
# 음슴체(~음/~함) 판정용 ETN 표면형. '기' 명사화(~하기)는 음슴체가 아니므로 제외.
EUMSEUM_ETN_FORMS = {"음", "ㅁ", "ᆷ"}

QUOTE_CHARS = '"\'“”‘’「」『』'
TERMINAL_CHARS = set('.!?…”’"\')」』~ㅋㅎ')

FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.S)
TIMESTAMP_LINE_RE = re.compile(r"^\(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\)\s*$")
KHOO_RE = re.compile(r"[ㅋㅎ]{2,}")
ELONG_SYL_RE = re.compile(r"([가-힣])\1{2,}")
ELONG_VOWEL_RE = re.compile(r"[ㅏㅑㅓㅕㅗㅛㅜㅠㅡㅣㅐㅔㅒㅖㅘㅙㅚㅝㅞㅟㅢ]{3,}")
DOT_RUN_RE = re.compile(r"\.{2,}")
ELLIPSIS_UNI_RE = re.compile(r"…+|·{3,}")
BOLD_RE = re.compile(r"\*\*[^*]+\*\*")
BULLET_LINE_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s+")
HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s+")
HAPSYO_END_RE = re.compile(r"(습니다|습니까|ㅂ니다|ㅂ니까|시오|십시오)$")

LINE_RHYTHM_KEYS = [
    "line_char_mean", "line_char_p50", "line_char_p90",
    "mid_sentence_break_ratio", "blank_line_ratio",
]

kiwi = Kiwi()


# --------------------------------------------------------------------------
# 로딩 / 전처리
# --------------------------------------------------------------------------

def strip_frontmatter(raw: str) -> str:
    return FRONTMATTER_RE.sub("", raw, count=1).strip()


def strip_reply_timestamps(text: str) -> str:
    """threads-reply 파일의 '(YYYY-MM-DD HH:MM)' 타임스탬프 라인은 크롤러 메타데이터이지
    저자 발화가 아니므로 줄바꿈 리듬/구두점 프로필 오염을 막기 위해 제거한다."""
    lines = [l for l in text.split("\n") if not TIMESTAMP_LINE_RE.match(l.strip())]
    joined = "\n".join(lines)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


def eojeol_list(text: str):
    return [w for w in re.split(r"\s+", text.strip()) if w]


def load_gn_docs():
    docs = []
    manifest_path = CORPUS / "manifest.jsonl"
    with manifest_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if not d.get("included") or d.get("split") != "train":
                continue
            if d["id"] in EXCLUDE_IDS:
                continue
            source = d["source"]
            dirpath = SOURCE_DIR.get(source)
            if dirpath is None:
                print(f"WARN: unknown source '{source}' for {d['id']}, skip", file=sys.stderr)
                continue
            path = dirpath / f"{d['id']}.md"
            if not path.exists():
                print(f"WARN: missing file {path}", file=sys.stderr)
                continue
            raw = path.read_text(encoding="utf-8")
            body = strip_frontmatter(raw)
            if source == "threads-reply":
                body = strip_reply_timestamps(body)
            if not body.strip():
                print(f"WARN: empty body after cleaning: {d['id']}", file=sys.stderr)
                continue
            group = SOURCE_GROUP.get(source, source)  # v4를 기존 셀에 합류
            channel = "brunch" if group == "brunch" else "threads"
            docs.append({
                "id": d["id"], "source": group, "genre": d.get("genre"),
                "channel": channel, "text": body,
            })
    return docs


def load_ref_docs():
    docs = []
    any_manifest = False
    for t in REF_TYPES:
        mpath = CORPUS / "reference" / f"manifest-{t}.jsonl"
        dpath = CORPUS / "reference" / t
        if not mpath.exists():
            print(f"WARN: reference manifest missing for type={t} ({mpath}) — 아직 수집 중일 수 있음, 스킵", file=sys.stderr)
            continue
        any_manifest = True
        with mpath.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                path = dpath / f"{d['id']}.md"
                if not path.exists():
                    print(f"WARN: missing ref file {path}", file=sys.stderr)
                    continue
                raw = path.read_text(encoding="utf-8")
                body = strip_frontmatter(raw)
                if not body.strip():
                    continue
                docs.append({"id": d["id"], "type": t, "channel": "ref", "text": body})
    if not any_manifest:
        print("WARN: 대조군 manifest가 전혀 없습니다 (수집 중). --corpus ref 스킵.", file=sys.stderr)
    return docs, any_manifest


# --------------------------------------------------------------------------
# 통계 유틸
# --------------------------------------------------------------------------

def pctl(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    d0 = s[f] * (c - k)
    d1 = s[c] * (k - f)
    return float(d0 + d1)


def stat_block(values):
    vals = list(values)
    n = len(vals)
    return {
        "mean": statistics.mean(vals) if n else 0.0,
        "std": statistics.pstdev(vals) if n > 1 else 0.0,
        "p10": pctl(vals, 10),
        "p50": pctl(vals, 50),
        "p90": pctl(vals, 90),
        "n": n,
    }


# --------------------------------------------------------------------------
# 문서 단위 피처
# --------------------------------------------------------------------------

def classify_register(sent_text, tokens_in_sent):
    ef_tokens = [t for t in tokens_in_sent if t.tag == "EF"]
    stripped = sent_text.rstrip()
    last_char = stripped[-1] if stripped else ""
    is_question = last_char == "?"
    is_exclaim = last_char == "!"
    if ef_tokens:
        form = ef_tokens[-1].form
        if form in EUMSEUM_ETN_FORMS:
            # 계사 음슴체(~임/~함): Kiwi가 이/VCP + ᆷ/EF 로 분석하는 경우
            reg = "nominal_etn"
        elif HAPSYO_END_RE.search(form):
            reg = "hapsyo"
        elif form.endswith("요") or form.endswith("죠"):
            reg = "haeyo"
        else:
            reg = "haeche_handa"
    else:
        lex_tokens = [t for t in tokens_in_sent if t.tag not in SYMBOL_SKIP_TAGS]
        last = lex_tokens[-1] if lex_tokens else None
        if last is not None and last.tag == "ETN" and last.form in EUMSEUM_ETN_FORMS:
            reg = "nominal_etn"  # 음슴체 종결(~음/~함): 캐주얼 반말 클러스터
        elif last is not None and last.tag in NOMINAL_END_TAGS:
            reg = "nominal_bare"  # 개조식 명사 종결: 워싱 제거 대상 축
        else:
            reg = "other"
    return reg, is_question, is_exclaim


def compute_doc_features(text):
    text = text.strip()
    if not text:
        return None
    n_chars = len(text)
    tokens = kiwi.tokenize(text)
    total_tokens = len(tokens) or 1

    sent_objs = [s for s in kiwi.split_into_sents(text) if s.text.strip()]
    sent_token_lists = []
    for s in sent_objs:
        toks = [t for t in tokens if s.start <= t.start < s.end]
        sent_token_lists.append((s.text, toks))
    n_sent = len(sent_token_lists) or 1

    sent_lens = [len(eojeol_list(stext)) for stext, _ in sent_token_lists]
    sent_lens = [x for x in sent_lens if x > 0]

    reg_counts = Counter()
    q_count = 0
    e_count = 0
    for stext, toks in sent_token_lists:
        reg, is_q, is_e = classify_register(stext, toks)
        reg_counts[reg] += 1
        if is_q:
            q_count += 1
        if is_e:
            e_count += 1

    ef_counter = Counter(t.form for t in tokens if t.tag == "EF")

    e_bigram_counter = Counter()
    for i in range(len(tokens) - 1):
        if tokens[i].tag in E_FAMILY and tokens[i + 1].tag in E_FAMILY:
            e_bigram_counter[(tokens[i].form, tokens[i + 1].form)] += 1

    pos_trigram_counter = Counter()
    for i in range(len(tokens) - 2):
        pos_trigram_counter[(tokens[i].tag, tokens[i + 1].tag, tokens[i + 2].tag)] += 1

    ic_ratio = sum(1 for t in tokens if t.tag == "IC") / total_tokens

    fp_na = sum(1 for t in tokens if t.tag == "NP" and t.form == "나")
    fp_jeo = sum(1 for t in tokens if t.tag == "NP" and t.form == "저")
    fp_pilja = sum(1 for t in tokens if t.tag == "NNG" and t.form == "필자")

    dm_counts = {m: 0 for m in DISCOURSE_MARKERS}
    for t in tokens:
        if t.form in dm_counts:
            dm_counts[t.form] += 1

    words = eojeol_list(text)
    n_words = len(words) or 1

    if len(words) >= 500:
        usable = len(words) - (len(words) % 500)
        chunks = [words[i:i + 500] for i in range(0, usable, 500)]
        ttrs = [len(set(c)) / len(c) for c in chunks if len(c) == 500]
        ttr = statistics.mean(ttrs) if ttrs else 0.0
        ttr_short = False
    else:
        ttr = (len(set(words)) / len(words)) if words else 0.0
        ttr_short = True

    ellipsis_formal = len(ELLIPSIS_UNI_RE.findall(text))
    ellipsis_informal = len(DOT_RUN_RE.findall(text))
    text_wo_ellipsis = ELLIPSIS_UNI_RE.sub("", text)
    text_wo_ellipsis = DOT_RUN_RE.sub("", text_wo_ellipsis)
    period = text_wo_ellipsis.count(".")
    comma = text.count(",")
    question_mark = text.count("?")
    exclaim_mark = text.count("!")
    tilde = text.count("~")
    quote = sum(text.count(c) for c in QUOTE_CHARS)

    kh_hits = len(KHOO_RE.findall(text))
    elong_hits = len(ELONG_SYL_RE.findall(text)) + len(ELONG_VOWEL_RE.findall(text))

    bold_hits = len(BOLD_RE.findall(text))
    lines_all = text.split("\n")
    bullet_hits = sum(1 for l in lines_all if BULLET_LINE_RE.match(l))
    heading_hits = sum(1 for l in lines_all if HEADING_LINE_RE.match(l))

    non_empty_lines = [l for l in lines_all if l.strip()]
    line_char_counts = [len(l.strip()) for l in non_empty_lines]
    mid_break = 0
    for l in non_empty_lines:
        s = l.rstrip()
        if s and s[-1] not in TERMINAL_CHARS:
            mid_break += 1
    mid_break_ratio = mid_break / len(non_empty_lines) if non_empty_lines else 0.0
    blank_lines = sum(1 for l in lines_all if not l.strip())
    blank_line_ratio = blank_lines / len(lines_all) if lines_all else 0.0

    def per1k_chars(c):
        return c / n_chars * 1000 if n_chars else 0.0

    def per1k_words(c):
        return c / n_words * 1000 if n_words else 0.0

    def per1k_tok(c):
        return c / total_tokens * 1000 if total_tokens else 0.0

    feats = {
        "n_chars": float(n_chars),
        "n_words": float(n_words),
        "n_tokens": float(total_tokens),
        "n_sent": float(n_sent),
        "sent_len_mean": statistics.mean(sent_lens) if sent_lens else 0.0,
        "sent_len_std": statistics.pstdev(sent_lens) if len(sent_lens) > 1 else 0.0,
        "sent_len_p10": pctl(sent_lens, 10),
        "sent_len_p50": pctl(sent_lens, 50),
        "sent_len_p90": pctl(sent_lens, 90),
        "reg_hapsyo_ratio": reg_counts.get("hapsyo", 0) / n_sent,
        "reg_haeyo_ratio": reg_counts.get("haeyo", 0) / n_sent,
        "reg_haeche_handa_ratio": reg_counts.get("haeche_handa", 0) / n_sent,
        "reg_nominal_ratio": (reg_counts.get("nominal_etn", 0) + reg_counts.get("nominal_bare", 0)) / n_sent,
        "reg_nominal_etn_ratio": reg_counts.get("nominal_etn", 0) / n_sent,
        "reg_nominal_bare_ratio": reg_counts.get("nominal_bare", 0) / n_sent,
        "reg_other_ratio": reg_counts.get("other", 0) / n_sent,
        "sent_end_question_ratio": q_count / n_sent,
        "sent_end_exclaim_ratio": e_count / n_sent,
        "punct_comma_per1k": per1k_chars(comma),
        "punct_period_per1k": per1k_chars(period),
        "punct_question_per1k": per1k_chars(question_mark),
        "punct_exclaim_per1k": per1k_chars(exclaim_mark),
        "punct_ellipsis_formal_per1k": per1k_chars(ellipsis_formal),
        "punct_ellipsis_informal_per1k": per1k_chars(ellipsis_informal),
        "punct_tilde_per1k": per1k_chars(tilde),
        "punct_quote_per1k": per1k_chars(quote),
        "kh_density_per1k": per1k_chars(kh_hits),
        "ic_ratio": ic_ratio,
        "elongation_per1k": per1k_chars(elong_hits),
        "line_char_mean": statistics.mean(line_char_counts) if line_char_counts else 0.0,
        "line_char_p50": pctl(line_char_counts, 50),
        "line_char_p90": pctl(line_char_counts, 90),
        "mid_sentence_break_ratio": mid_break_ratio,
        "blank_line_ratio": blank_line_ratio,
        "fp_na_per1k": per1k_words(fp_na),
        "fp_jeo_per1k": per1k_words(fp_jeo),
        "fp_pilja_per1k": per1k_words(fp_pilja),
        "ttr": ttr,
        "ttr_short_doc": ttr_short,
        "bold_density_per1k": per1k_chars(bold_hits),
        "bullet_density_per1k": per1k_chars(bullet_hits),
        "heading_density_per1k": per1k_chars(heading_hits),
    }
    for m in DISCOURSE_MARKERS:
        feats[f"dm_{m}_per1k"] = per1k_words(dm_counts[m])
    for jt in J_TAGS:
        cnt = sum(1 for t in tokens if t.tag == jt)
        feats[f"josa_{jt}_per1k_tok"] = per1k_tok(cnt)

    return {
        "feats": feats,
        "ef_counter": ef_counter,
        "e_bigram_counter": e_bigram_counter,
        "pos_trigram_counter": pos_trigram_counter,
    }


# --------------------------------------------------------------------------
# 셀 집계
# --------------------------------------------------------------------------

def top_n_from_counter(counter, n, key_to_str):
    total = sum(counter.values())
    items = counter.most_common(n)
    return {
        "total_count": total,
        "top": [
            {"key": key_to_str(k), "count": c, "rel_freq": (c / total) if total else 0.0}
            for k, c in items
        ],
    }


def aggregate_cell(entries, split_channel):
    """entries: list of {id, channel, feats, ef_counter, e_bigram_counter, pos_trigram_counter}"""
    n = len(entries)
    if n == 0:
        return None
    feats_list = [e["feats"] for e in entries]
    numeric_keys = [
        k for k, v in feats_list[0].items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    agg_keys = [k for k in numeric_keys if k not in LINE_RHYTHM_KEYS]

    features = {}
    for k in agg_keys:
        features[k] = stat_block(f[k] for f in feats_list)

    short_flags = [f.get("ttr_short_doc", False) for f in feats_list]
    features["_ttr_short_doc_fraction"] = sum(1 for x in short_flags if x) / n

    line_rhythm_by_channel = {}
    channels = sorted(set(e["channel"] for e in entries)) if split_channel else [entries[0]["channel"]]
    for ch in channels:
        sub = [e["feats"] for e in entries if e["channel"] == ch] if split_channel else feats_list
        if not sub:
            continue
        block = {k: stat_block(f[k] for f in sub) for k in LINE_RHYTHM_KEYS}
        block["n"] = len(sub)
        line_rhythm_by_channel[ch] = block

    ef_sum = Counter()
    e_bigram_sum = Counter()
    pos_trigram_sum = Counter()
    for e in entries:
        ef_sum.update(e["ef_counter"])
        e_bigram_sum.update(e["e_bigram_counter"])
        pos_trigram_sum.update(e["pos_trigram_counter"])

    total_words = sum(f["n_words"] for f in feats_list)
    total_chars = sum(f["n_chars"] for f in feats_list)
    total_tokens = sum(f["n_tokens"] for f in feats_list)

    return {
        "n": n,
        "doc_ids": sorted(e["id"] for e in entries),
        "total_words": int(total_words),
        "total_chars": int(total_chars),
        "total_tokens": int(total_tokens),
        "features": features,
        "line_rhythm_by_channel": line_rhythm_by_channel,
        "ef_top20": top_n_from_counter(ef_sum, 20, lambda k: k),
        "e_bigram_top30": top_n_from_counter(e_bigram_sum, 30, lambda k: f"{k[0]}+{k[1]}"),
        "pos_trigram_top50": top_n_from_counter(pos_trigram_sum, 50, lambda k: "-".join(k)),
    }


# --------------------------------------------------------------------------
# 모드: gn
# --------------------------------------------------------------------------

def run_corpus_gn():
    docs = load_gn_docs()
    if not docs:
        sys.exit("gn train 문서를 하나도 찾지 못했습니다. corpus/manifest.jsonl 및 디렉토리를 확인하십시오.")

    entries = []
    for d in docs:
        r = compute_doc_features(d["text"])
        if r is None:
            continue
        entries.append({
            "id": d["id"], "source": d["source"], "genre": d["genre"],
            "channel": d["channel"], **r,
        })

    cells = {}
    cells["author_pooled"] = aggregate_cell(entries, split_channel=True)

    reply_entries = [e for e in entries if e["source"] == "threads-reply"]
    if reply_entries:
        cells["reply"] = aggregate_cell(reply_entries, split_channel=False)

    source_genre_groups = {}
    for e in entries:
        if e["genre"] is None:
            continue
        key = (e["source"], e["genre"])
        source_genre_groups.setdefault(key, []).append(e)
    for (source, genre), grp in sorted(source_genre_groups.items()):
        cells[f"source_genre:{source}/{genre}"] = aggregate_cell(grp, split_channel=False)

    genre_groups = {}
    for e in entries:
        if e["genre"] is None:
            continue
        genre_groups.setdefault(e["genre"], []).append(e)
    for genre, grp in sorted(genre_groups.items()):
        cells[f"genre:{genre}"] = aggregate_cell(grp, split_channel=True)

    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "corpus": "gn",
            "kiwi_version": __import__("kiwipiepy").__version__,
            "manifest": "corpus/manifest.jsonl",
            "filter": "included==true && split=='train'",
            "excluded_ids": sorted(EXCLUDE_IDS),
            "n_docs_total": len(entries),
            "cell_list": sorted(cells.keys()),
        },
        "cells": cells,
    }

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ANALYSIS_DIR / "fingerprint-gn.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: wrote {out_path} ({len(entries)} docs, {len(cells)} cells)", file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# 모드: ref
# --------------------------------------------------------------------------

def run_corpus_ref():
    docs, any_manifest = load_ref_docs()
    if not any_manifest or not docs:
        print("WARN: 대조군 코퍼스가 없어 --corpus ref 를 스킵합니다.", file=sys.stderr)
        return None

    entries = []
    for d in docs:
        r = compute_doc_features(d["text"])
        if r is None:
            continue
        entries.append({"id": d["id"], "type": d["type"], "channel": d["channel"], **r})

    cells = {}
    cells["ref_pooled"] = aggregate_cell(entries, split_channel=False)
    type_groups = {}
    for e in entries:
        type_groups.setdefault(e["type"], []).append(e)
    for t, grp in sorted(type_groups.items()):
        cells[f"type:{t}"] = aggregate_cell(grp, split_channel=False)

    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "corpus": "ref",
            "kiwi_version": __import__("kiwipiepy").__version__,
            "manifest": [f"corpus/reference/manifest-{t}.jsonl" for t in REF_TYPES],
            "n_docs_total": len(entries),
            "cell_list": sorted(cells.keys()),
        },
        "cells": cells,
    }

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ANALYSIS_DIR / "fingerprint-ref.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: wrote {out_path} ({len(entries)} docs, {len(cells)} cells)", file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# 모드: calibrate
# --------------------------------------------------------------------------

def zscore(gn_mean, ref_mean, ref_std):
    if ref_std is None or ref_std == 0:
        return None
    return (gn_mean - ref_mean) / ref_std


def sign(x):
    if x > 0:
        return "+"
    if x < 0:
        return "-"
    return "0"


def compare_features(gn_features, ref_features, gn_n):
    out = {}
    for k, gn_block in gn_features.items():
        if k.startswith("_"):
            continue
        ref_block = ref_features.get(k)
        if ref_block is None:
            continue
        gn_mean = gn_block["mean"]
        ref_mean = ref_block["mean"]
        ref_std = ref_block["std"]
        if gn_n < 10:
            out[k] = {
                "direction": sign(gn_mean - ref_mean),
                "range_suppressed": True,
                "note": f"gn cell n={gn_n} < 10 — z 수치(range) 금지, 방향만 기록",
            }
        else:
            z = zscore(gn_mean, ref_mean, ref_std)
            out[k] = {
                "z": z,
                "gn_mean": gn_mean,
                "ref_mean": ref_mean,
                "ref_std": ref_std,
                "range_suppressed": False,
            }
    return out


def compare_line_rhythm(gn_lr, ref_lr, gn_n):
    """gn_lr: {channel: block}, ref는 항상 단일 채널 'ref' 이므로 gn의 모든 채널을
    ref['ref'] 하나와 비교한다 (대조군엔 threads/brunch 구분 개념이 없음)."""
    if "ref" not in ref_lr:
        return {}
    ref_block = ref_lr["ref"]
    out = {}
    for ch, gnb in gn_lr.items():
        out[ch] = {}
        for k in LINE_RHYTHM_KEYS:
            gn_mean = gnb[k]["mean"]
            ref_mean = ref_block[k]["mean"]
            ref_std = ref_block[k]["std"]
            if gn_n < 10:
                out[ch][k] = {
                    "direction": sign(gn_mean - ref_mean),
                    "range_suppressed": True,
                    "note": f"gn cell n={gn_n} < 10 — z 수치(range) 금지, 방향만 기록",
                }
            else:
                out[ch][k] = {
                    "z": zscore(gn_mean, ref_mean, ref_std),
                    "gn_mean": gn_mean,
                    "ref_mean": ref_mean,
                    "ref_std": ref_std,
                    "range_suppressed": False,
                }
    return out


def run_calibrate():
    gn_path = ANALYSIS_DIR / "fingerprint-gn.json"
    ref_path = ANALYSIS_DIR / "fingerprint-ref.json"
    if not gn_path.exists():
        sys.exit(f"fingerprint-gn.json 이 없습니다. 먼저 --corpus gn 실행하십시오. ({gn_path})")
    if not ref_path.exists():
        sys.exit(
            f"fingerprint-ref.json 이 없습니다 ({ref_path}). "
            "대조군 코퍼스가 아직 수집 중이면 --corpus ref 를 먼저 실행해야 하며, "
            "대조군 자체가 없으면 calibrate 는 수행할 수 없습니다."
        )

    gn = json.loads(gn_path.read_text(encoding="utf-8"))
    ref = json.loads(ref_path.read_text(encoding="utf-8"))

    ref_type_cells = {
        k.split(":", 1)[1]: v for k, v in ref["cells"].items()
        if k.startswith("type:") and v is not None
    }
    if "ref_pooled" in ref["cells"] and ref["cells"]["ref_pooled"] is not None:
        ref_type_cells["pooled"] = ref["cells"]["ref_pooled"]

    if not ref_type_cells:
        sys.exit("fingerprint-ref.json 에 유효한 대조군 셀이 없습니다. calibrate 불가.")

    zprofile = {}
    for gn_cell_name, gn_cell in gn["cells"].items():
        if gn_cell is None:
            continue
        gn_n = gn_cell["n"]
        zprofile[gn_cell_name] = {"n": gn_n, "vs_ref_type": {}}
        for ref_type_name, ref_cell in ref_type_cells.items():
            if ref_cell is None:
                continue
            feat_cmp = compare_features(gn_cell["features"], ref_cell["features"], gn_n)
            lr_cmp = compare_line_rhythm(
                gn_cell.get("line_rhythm_by_channel", {}),
                ref_cell.get("line_rhythm_by_channel", {}),
                gn_n,
            )
            zprofile[gn_cell_name]["vs_ref_type"][ref_type_name] = {
                "features": feat_cmp,
                "line_rhythm": lr_cmp,
            }

    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gn_source": str(gn_path.relative_to(ROOT)),
            "ref_source": str(ref_path.relative_to(ROOT)),
            "ref_types_used": sorted(ref_type_cells.keys()),
            "n_lt_10_policy": "direction(+/-/0)만 기록, z range 금지",
            "note_distributional": (
                "ef_top20/e_bigram_top30/pos_trigram_top50 은 top-N 랭킹 특성상 "
                "z-스코어 대상에서 제외(스칼라 mean/std 피처만 z 산출)."
            ),
        },
        "zprofile": zprofile,
    }

    ZPROFILE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ZPROFILE_DIR / "zprofile.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: wrote {out_path} ({len(zprofile)} gn cells x {len(ref_type_cells)} ref types)", file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="저자 빌드타임 정량 문체 분석")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--corpus", choices=["gn", "ref"], help="분석 대상 코퍼스 (기본 gn)")
    g.add_argument("--calibrate", action="store_true", help="gn/ref json으로 z-프로필 산출")
    args = ap.parse_args()

    if args.calibrate:
        run_calibrate()
    elif args.corpus == "ref":
        run_corpus_ref()
    else:
        run_corpus_gn()


if __name__ == "__main__":
    main()
