#!/usr/bin/env python3
"""fingerprint-slim.json 생성기 (빌드타임) — 런타임 검증 밴드의 단일 재현 경로.

핵심 설계: 밴드를 fingerprint.py(kiwipiepy) 수치에서 베끼지 않고,
**런타임 verify_style.py와 동일한 정규식 계측기**로 코퍼스 문서별 지표를
직접 재서 운영 분위수(p5/p50/p95)를 산출한다. 빌드(kiwi) vs 런타임(regex) 계측기
불일치로 ±slack 땜빵하던 구조적 오차를 제거하기 위함(2026-07-02 감사 결함 #3).
운영점은 CV winner(q5_95-range2-sm1p2): 스키마 호환을 위해 p10/p90 키를 유지하되
전 장르 밴드 값은 5/95 분위수로 산출한다.

장르 축은 3축 재분류(채널×길이) 기준: biz는 biz-s(threads)/biz-l(brunch)로
분리, reply 신설, essay/lecture는 brunch 셀 전용.

사용: ~/.venvs/gn/bin/python scripts/make_slim.py
(fingerprint.py를 import하므로 kiwipiepy가 있는 venv 필요 — 코퍼스 로더 재사용 목적)
"""
import importlib.util
import argparse
import hashlib
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "gn-voice"
if not SKILL.exists():
    SKILL = ROOT  # 공개 레포 레이아웃 — 스킬(SKILL.md·references/)이 저장소 루트
OUT = SKILL / "references" / "fingerprint-slim.json"
FPGN = ROOT / "analysis" / "quant" / "fingerprint-gn.json"
MANIFEST = ROOT / "corpus" / "manifest.jsonl"

sys.path.insert(0, str(ROOT / "scripts"))
import fingerprint as fp  # noqa: E402  (load_gn_docs 재사용 — kiwi 로드됨, 빌드타임 전용)

_spec = importlib.util.spec_from_file_location("verify_style", SKILL / "scripts" / "verify_style.py")
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)

# 장르 → (fingerprint 셀 이름, 문서 필터). 셀 이름은 line_rhythm 복사용.
GENRES = {
    "chat":   ("source_genre:threads/chat",   lambda d: d["source"] == "threads" and d["genre"] == "chat"),
    "lecture-tip": ("source_genre:threads/lecture-tip", lambda d: d["source"] == "threads" and d["genre"] == "lecture-tip"),
    "promo":  ("source_genre:threads/promo",  lambda d: d["source"] == "threads" and d["genre"] == "promo"),
    "reply":  ("reply",                        lambda d: d["source"] == "threads-reply"),
    "essay":  ("source_genre:brunch/essay",    lambda d: d["source"] == "brunch" and d["genre"] == "essay"),
    "lecture": ("source_genre:brunch/lecture", lambda d: d["source"] == "brunch" and d["genre"] == "lecture"),
    "biz-s":  ("source_genre:threads/biz",     lambda d: d["source"] == "threads" and d["genre"] == "biz"),
    "biz-l":  ("source_genre:brunch/biz",      lambda d: d["source"] == "brunch" and d["genre"] == "biz"),
    "column": ("source_genre:brunch/column",   lambda d: d["source"] == "brunch" and d["genre"] == "column"),
}

MIN_BAND_N = 10  # 미만이면 direction_only (verify_style은 p10 없는 지표를 검사하지 않음)
BAND_QUANTILES = (5, 95)
L_SMOOTHNESS_UNION_GENRES = {"essay", "lecture", "column"}
NEW_ERA_NOTE = "brunch-lt-2026-07"
DEFAULT_CORPUS_ID = "gn-v4"
KST = timezone(timedelta(hours=9))
PERIOD_RUN_RE = re.compile(r"\.{2,}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build fingerprint-slim.json")
    parser.add_argument(
        "--corpus-id",
        default=DEFAULT_CORPUS_ID,
        help=f"corpus generation id (default: {DEFAULT_CORPUS_ID})",
    )
    return parser.parse_args(argv)


def corpus_snapshot(corpus_id, tool):
    data = MANIFEST.read_bytes()
    n_docs = sum(1 for line in data.splitlines() if line.strip())
    return {
        "id": corpus_id,
        "manifest_sha256": hashlib.sha256(data).hexdigest(),
        "n_docs": n_docs,
        "built": datetime.now(KST).replace(microsecond=0).isoformat(),
        "tool": tool,
    }


def pctl(vals, p):
    return fp.pctl(vals, p)


def agg(values, banded):
    values = [v for v in values if v is not None]
    if not values:
        return None
    out = {"mean": round(statistics.mean(values), 3)}
    if banded:
        qlo, qhi = BAND_QUANTILES
        out["p10"] = round(pctl(values, qlo), 3)
        out["p90"] = round(pctl(values, qhi), 3)
    return out


def compute_for_band(body):
    """Keep established slim bands stable by aggregating period density before per-doc rounding."""
    metrics = vs.compute(body)
    n_chars = max(len(body), 1)
    metrics["punct_period_per1k"] = (
        (body.count(".") - len(PERIOD_RUN_RE.findall(body)) * 2) / n_chars * 1000.0
    )
    return metrics


def load_train_era_map():
    era_by_id = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("included") or row.get("split") != "train":
            continue
        note = row.get("note") or ""
        era_by_id[row["id"]] = "B" if NEW_ERA_NOTE in note else "A"
    return era_by_id


def smoothness_block(vals, low_p, high_p):
    return {
        "p10": round(pctl(vals, low_p), 3),
        "p50": round(pctl(vals, 50), 3),
        "p90": round(pctl(vals, high_p), 3),
        "n": len(vals),
    }


def main(argv=None):
    args = parse_args(argv)
    docs = fp.load_gn_docs()
    era_by_id = load_train_era_map()
    fpgn = json.loads(FPGN.read_text(encoding="utf-8"))
    genres_out = {}
    for genre, (cell_name, pred) in GENRES.items():
        grp = [d for d in docs if pred(d)]
        if not grp:
            print(f"WARN: {genre} 문서 0건 — 건너뜀", file=sys.stderr)
            continue
        n = len(grp)
        banded = n >= MIN_BAND_N
        per_doc = [compute_for_band(d["text"]) for d in grp]
        per_doc_sm = [(d["id"], vs.smoothness(d["text"])) for d in grp]
        metric_keys = per_doc[0].keys()
        metrics = {}
        for k in metric_keys:
            a = agg([m[k] for m in per_doc], banded)
            if a is not None:
                metrics[k] = a
        smooth = {}
        # CV winner 반영: 전 장르 p5/p95를 기존 p10/p90 키에 담아 스키마를 유지한다.
        smooth_low_p, smooth_high_p = BAND_QUANTILES
        for k in ("ending_entropy", "sent_len_cv", "line_len_cv"):
            vals = [m[k] for _, m in per_doc_sm if k in m]
            if len(vals) >= 4:
                smooth[k] = smoothness_block(vals, smooth_low_p, smooth_high_p)
                # 시점 편향 교정: era-union (fable 처분 2026-07-03)
                if genre in L_SMOOTHNESS_UNION_GENRES:
                    era_vals = {"A": [], "B": []}
                    for doc_id, m in per_doc_sm:
                        if k in m:
                            era_vals[era_by_id.get(doc_id, "A")].append(m[k])
                    if len(era_vals["A"]) >= 4 and len(era_vals["B"]) >= 4:
                        lo = min(pctl(era_vals["A"], smooth_low_p), pctl(era_vals["B"], smooth_low_p))
                        hi = max(pctl(era_vals["A"], smooth_high_p), pctl(era_vals["B"], smooth_high_p))
                        smooth[k]["p10"] = round(lo, 3)
                        smooth[k]["p90"] = round(hi, 3)
        cell = fpgn["cells"].get(cell_name) or {}
        genres_out[genre] = {
            "n": n,
            "range_mode": "p10p90" if banded else "direction_only",
            "source_cell": cell_name,
            "metrics": metrics,
            "line_rhythm": cell.get("line_rhythm_by_channel", {}),
            "smoothness": smooth,
        }
    out = {
        "meta": {
            "generator": "scripts/make_slim.py",
            "corpus_snapshot": corpus_snapshot(args.corpus_id, Path(__file__).name),
            "note": (
                "런타임 검증 전용. 밴드는 verify_style.py와 동일한 정규식 계측기로 "
                "코퍼스 문서별 재계산한 값 — kiwi 기반 fingerprint-gn.json과 절대값이 "
                "다른 것이 정상(계측기 통일이 목적). reg_eumseum_ratio는 음슴체 근사. "
                "n<10 장르는 direction_only(범위 fail 없음)."
            ),
            "must_zero": [
                "kh_density_per1k", "elongation_per1k",
                "bold_density_per1k", "heading_density_per1k",
            ],
            "smoothness_note": (
                "윤문티 방지: ending_entropy·sent_len_cv·line_len_cv가 p10 미만이면 "
                "'너무 매끈' 위반(하한 검사). p10/p90 키에는 CV winner의 p5/p95 값 저장."
            ),
            "quantile_note": "CV winner q5_95-range2-sm1p2 반영: 전 장르 밴드 p5/p95.",
        },
        "genres": genres_out,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    summary = {g: (v["n"], v["range_mode"]) for g, v in genres_out.items()}
    print(f"OK: wrote {OUT} — {summary}")


if __name__ == "__main__":
    main()
