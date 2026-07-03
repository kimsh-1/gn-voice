#!/usr/bin/env bash
# gn-voice 설치 — 공개판 스킬·에이전트를 ~/.claude 에 심링크
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$HOME/.claude/skills/gn-voice"
AGENTS_DIR="$HOME/.claude/agents"

link_path() {
  local target="$1"
  local link="$2"

  if [ ! -e "$target" ]; then
    echo "FAIL: missing target: $target" >&2
    exit 1
  fi
  if [ -e "$link" ] && [ ! -L "$link" ]; then
    echo "FAIL: not a symlink: $link" >&2
    exit 1
  fi
  ln -sfn "$target" "$link"
}

if [ -L "$SKILL_DIR" ]; then
  rm "$SKILL_DIR"
elif [ -e "$SKILL_DIR" ] && [ ! -d "$SKILL_DIR" ]; then
  echo "FAIL: not a directory: $SKILL_DIR" >&2
  exit 1
fi

mkdir -p "$SKILL_DIR" "$AGENTS_DIR"

link_path "$ROOT/SKILL.md" "$SKILL_DIR/SKILL.md"
link_path "$ROOT/references" "$SKILL_DIR/references"
link_path "$ROOT/scripts" "$SKILL_DIR/scripts"

shopt -s nullglob
agent_names=()
for agent in "$ROOT"/agents/*.md; do
  name="$(basename "$agent")"
  link_path "$agent" "$AGENTS_DIR/$name"
  agent_names+=("${name%.md}")
done
shopt -u nullglob

echo "OK: gn-voice 설치 완료"
echo "  skill : $SKILL_DIR/{SKILL.md,references,scripts}"
if [ "${#agent_names[@]}" -gt 0 ]; then
  printf "  agents: %s\n" "${agent_names[*]}"
else
  echo "  agents: none"
fi
