#!/usr/bin/env python3
"""gn-voice 게이트 1/2 — 명제 자산 보존 검사 (stdlib only, 결정론).

윤문 전후로 '지어내면 안 되고 잃어버려도 안 되는' 자산을 대조한다:
  - 수치(아라비아 숫자, 단위 포함 토큰)
  - 라틴 고유명사(영문 토큰: 제품명·라이브러리명 등)
  - 퍼센트·금액·날짜 패턴

사용: python3 gate_fidelity.py <input.txt> <final.md>
출력: JSON 한 줄 {pass, missing: [...], invented: [...], stats}
- missing  = 원문에 있는데 출력에서 사라진 자산 (누락 — blocking)
- invented = 출력에만 있는 자산 (날조 의심 — blocking. 단 숫자 어림 표기
  변형은 사람이 판단하도록 목록만 제공)
한글 표기 수사(하나/둘/첫째)는 문체 변환에서 형태가 바뀔 수 있어 대상 외 —
이 게이트는 형태 불변이어야 할 자산만 본다.
"""
import json
import re
import sys
from pathlib import Path

SUMMARY_RE = re.compile(r"<!--\s*GNVOICE-SUMMARY.*?-->", re.S)
# 숫자+선택적 단위: 2주, 15시간, 44.9%, 2,500자, 3개 ...
NUM_RE = re.compile(r"\d[\d,.]*\s?[%가-힣a-zA-Z]{0,3}")
# 번호토막 마커(1./2. 붙여쓰기 포함): 공냥 스타일 장치라 수치 자산이 아님 — 대조 제외
LIST_MARKER_RE = re.compile(r"^\d{1,2}\.")
# 라틴 토큰(2자+): Kiwi, JSON, API ...
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,}")


def norm_num(tok: str) -> str:
    return tok.replace(",", "").replace(" ", "").strip(".")


def assets(text: str):
    text = SUMMARY_RE.sub("", text)
    nums = {norm_num(m.group()) for m in NUM_RE.finditer(text)
            if not LIST_MARKER_RE.match(m.group())}
    latin = {m.group().lower() for m in LATIN_RE.finditer(text)}
    return nums, latin


def main():
    src = Path(sys.argv[1]).read_text(encoding="utf-8")
    out = Path(sys.argv[2]).read_text(encoding="utf-8")
    src_n, src_l = assets(src)
    out_n, out_l = assets(out)

    # 숫자는 수치 부분만으로도 대조(단위 어절이 붙거나 떨어져도 수치 자체가 있으면 보존으로 침)
    def digits_only(s):
        return re.sub(r"[^\d.]", "", s)

    out_digits = {digits_only(x) for x in out_n}
    missing_n = sorted(x for x in src_n if digits_only(x) not in out_digits)
    invented_n = sorted(x for x in out_n if digits_only(x) not in {digits_only(y) for y in src_n})
    missing_l = sorted(src_l - out_l)
    invented_l = sorted(out_l - src_l)

    missing = missing_n + missing_l
    invented = invented_n + invented_l
    verdict = {
        "pass": not missing and not invented,
        "missing": missing,
        "invented": invented,
        "stats": {
            "src_nums": len(src_n), "src_latin": len(src_l),
            "out_nums": len(out_n), "out_latin": len(out_l),
        },
    }
    print(json.dumps(verdict, ensure_ascii=False))
    sys.exit(0 if verdict["pass"] else 1)


if __name__ == "__main__":
    main()
