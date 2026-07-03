#!/usr/bin/env bash
# gn-voice 제거 — install.sh가 만든 공개판 심링크만 안전 제거
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$HOME/.claude/skills/gn-voice"
AGENTS_DIR="$HOME/.claude/agents"

remove_link_if_target() {
  local link="$1"
  local target="$2"

  if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
    rm "$link"
    echo "removed: $link"
  fi
}

if [ -L "$SKILL_DIR" ] && [ "$(readlink "$SKILL_DIR")" = "$ROOT" ]; then
  rm "$SKILL_DIR"
  echo "removed: $SKILL_DIR"
else
  remove_link_if_target "$SKILL_DIR/SKILL.md" "$ROOT/SKILL.md"
  remove_link_if_target "$SKILL_DIR/references" "$ROOT/references"
  remove_link_if_target "$SKILL_DIR/scripts" "$ROOT/scripts"
fi

if [ -d "$SKILL_DIR" ] && ! [ -L "$SKILL_DIR" ]; then
  rmdir "$SKILL_DIR" 2>/dev/null || true
fi

shopt -s nullglob
for agent in "$ROOT"/agents/*.md; do
  remove_link_if_target "$AGENTS_DIR/$(basename "$agent")" "$agent"
done
shopt -u nullglob

echo "OK: gn-voice 제거 완료"
