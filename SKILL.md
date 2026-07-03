---
name: gn-voice
version: 3.1.0
description: >-
  AI가 쓴 한국어 글을 저자 본인 문체로 재작성하는 개인 전용 윤문 스킬.
  코퍼스 986편(threads+답글+브런치)에서 증류한 장르 팩(chat/reply/essay/lecture/biz-s/biz-l/column,
  3축 체계: 채널×길이 · 레지스터 · 워싱 수준) + 웰메이드 팩
  (연설문/설명문/주장문/카피/바이럴/명수필 — 성과 검증 대조군 실증)을 적용하고 stdlib 지표 검증까지 수행한다.
  트리거 — "저자체로", "저자 문체로", "내 문체로 바꿔", "내 말투로", "gn-voice", "저자 윤문",
  "팔리는 글로", "바이럴 글", "장표 텍스트로", "슬라이드 글로", "프레젠테이션용으로".
  후속 — "장르 바꿔서 다시", "clean 모드로", "연설문체로", "이 문단만 다시" 도 이 스킬.
  범용 "AI 티 제거"는 humanize-korean 스킬 소관 — 이 스킬은 저자 개인화 전용.
---

# gn-voice — 저자 문체 윤문

ROOT = 이 SKILL.md가 있는 디렉토리 (references/, scripts/ 포함)

## Phase 0 — 인자 파싱·워크스페이스
1. 입력: 인라인 텍스트 또는 파일 경로(파일이면 Read).
2. 옵션 파싱:
   - `--mode {compose|rewrite}` — **compose(기본, VOL2)**: 아이디어·초안을 재료로 웰메이드 글을 집필(약한 논거 폐기·재구성 허용, 웰메이드 > 보이스). **rewrite(VOL1)**: 명제 전량 보존 문체 윤문. 사용자가 "윤문해줘/문체만 바꿔줘"라고 하면 rewrite, "써줘/만들어줘/완성해줘"면 compose.
   - `--profile {copy|sns|launch|essay|column|lecture|expository|argumentative|prose|presentation|formal}` (compose 전용) — 실무 용도별 로직. 미지정 시 요청 문구로 추정: "팔릴/판매/카피"→copy, "스레드/포스트"→sns, "공지/모집"→launch, "브런치 에세이"→essay, "칼럼/분석"→column, "강의/튜토리얼/따라하기"→lecture, "설명문/교육/개념 설명"→expository, "논설문/사설/주장"→argumentative, "수필/문예/산문"→prose, "발표/PT/슬라이드/장표/발표자료"→presentation, "기고/보도자료/공문"→formal. 추정 결과를 실행 전 한 줄로 표기.
   - `--style {auto|chat|reply|essay|lecture|lecture-tip|promo|biz-s|biz-l|column|wm-speech|wm-expository|wm-argumentative|wm-copy|wm-sns|wm-prose}` (기본 auto. 구 값 `biz`는 판별 트리가 biz-s/biz-l 결정)
   - `--clean {preserve|moderate|clean}` (기본 preserve, rewrite 모드 전용)
   - `--wash {L0|L1|L2|L3}` (옵션) — 워싱 수준 축(SSOT: style-profile/axes-matrix.md). 미지정 시 스타일·프로파일 기본값:
     L0 티제거(formal·presentation) / L1 라이트(column·lecture·expository·argumentative·copy·launch) /
     L2 균형(essay·prose·sns — 전체 기본값) / L3 풀캐주얼(chat·reply — 음슴체·은어·`!!!!` 풀 점등).
3. run_id: cwd의 `_workspace/` 아래 `{YYYY-MM-DD-NNN}` (Glob으로 기존 `01_input.txt` 표지 매칭해 NNN 산출, Bash ls 금지).
4. `_workspace/{run_id}/01_input.txt`에 원문 저장.
5. 한 줄 출력: `gn-voice v1.0 — style:{값} clean:{값} run:{run_id}`

## Phase 1 — 에이전트 1콜
- **compose 모드**: `gn-voice-composer` 1회 호출 — 인자: input_path, compose_path={ROOT}/references/compose-core.md, pack_dir={ROOT}/references/packs, out_path, genre_hint={--style}, length_hint(사용자 지정 시).
- **rewrite 모드**: `gn-voice-monolith` 1회 호출. 프롬프트에 인자 명시:
```
input_path: {cwd}/_workspace/{run_id}/01_input.txt
core_path: {ROOT}/references/voice-core.md
pack_dir: {ROOT}/references/packs
verify_script: {ROOT}/scripts/verify_style.py
slim_json: {ROOT}/references/fingerprint-slim.json
out_path: {cwd}/_workspace/{run_id}/final.md
style_hint: {--style 값} / clean_level: {--clean 값} / wash_level: {--wash 값 또는 "auto"}
```

## Phase 1.5 — 심사 게이트 (rewrite 모드 전용, compose 모드는 Phase 2로 직행)
1. Phase 1(monolith) 완료 메시지의 verify 결과 확인. **fail**이면 심사 생략, Phase 2-2(기존 실패 처리)로 이동.
2. **pass**면 `gn-voice-judge` 1회 호출 — 인자: final_path=out_path, input_path, genre={완료 메시지의 장르}, pack_dir={ROOT}/references/packs, ai_tells_path={ROOT}/references/ai-tells.json, fingerprint_path={ROOT}/references/fingerprint-slim.json.
3. 등급 처리(재윤문은 전체 흐름에서 최대 1회):
   - **A/B** → Phase 2로 진행.
   - **C**이고 아직 재윤문 전이면 → judge의 재작업 지시문을 `gn-voice-monolith`에 rework_hint로 전달해 1회 재윤문(같은 run_id에 `final_v2.md`) → 게이트 통과 시 judge 재판정(1번으로 복귀, rework_hint는 재사용 안 함). 재판정이 A/B면 Phase 2, D면 아래 D 처리, 다시 C면(재윤문 예산 소진) 위반 사항을 명시한 채 Phase 2로 진행.
   - **D** → 등급과 위반 사항을 사용자에게 그대로 보고하고 중단(Phase 2 미실행).

## Phase 2 — 결과 전달
1. final.md를 Read해 본문을 사용자에게 제시(GNVOICE-SUMMARY 주석과 judge 등급·위반 사항이 있으면 표로 요약해 별도 표기).
2. verify fail이거나 confidence 低면 그 사실과 위반 지표를 명시하고, `--style` 수동 지정 재실행을 제안.
3. 후속 요청("이 문단만", "clean으로") 시 같은 run_id 폴더에 `final_v2.md`로 버전 분리.

## 규칙
- 코퍼스(corpus/)·풀 SSOT(style-profile/)는 이 스킬 어디서도 로드하지 않는다(빌드타임 전용).
- 메인 컨텍스트에는 final.md 본문 1회 Read만 허용(중간 산출물 로드 금지).
- monolith가 죽거나 빈 파일이면 1회 재호출, 그래도 실패면 실패 보고(수동 폴백 금지).
