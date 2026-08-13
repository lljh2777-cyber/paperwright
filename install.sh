#!/usr/bin/env bash
# Paper2MD 引导安装器 — 一键安装 CLI 与 Agent skills 到当前 harness
#
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/lljh2777-cyber/Paper2MD/main/install.sh | bash
#   bash install.sh [install|update|uninstall|verify] [选项]
#
# 选项:
#   --harness claude|codex|cursor|gemini|all|none   目标 agent（默认自动检测）
#   --python PATH        指定 Python 解释器（3.10–3.13）
#   --prefix DIR         安装根目录（默认 ~/.paper2md）
#   --no-skills          只装 CLI，不复制 skills
#   --local CHECKOUT     使用本地源码目录（跳过 clone）
#   --editable           可编辑安装（pip install -e）：源码改动即时生效，适合贡献者
#   --yes                非交互，全部用默认值
#
# 安装内容:
#   ~/.paper2md/src      源码 checkout
#   ~/.paper2md/venv     隔离虚拟环境（pip install .）
#   ~/.local/bin/paper2md CLI 符号链接
#   <harness>/skills/paper2md-*  4 个 Agent skills

set -euo pipefail

REPO_URL="https://github.com/lljh2777-cyber/Paper2MD.git"
REPO_BRANCH="main"
PREFIX="${P2MD_PREFIX:-$HOME/.paper2md}"
CHECKOUT_DIR="$PREFIX/src"
VENV_DIR="$PREFIX/venv"
BIN_LINK="${P2MD_BIN_LINK:-$HOME/.local/bin/paper2md}"
SKILL_DIRS=("paper2md-install" "paper2md-convert" "paper2md-contribute" "paper2md-agent-workflow")
NO_COLOR="${NO_COLOR:-}"

if [[ -z "$NO_COLOR" && -t 1 ]]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YEL=$'\033[33m'; C_BLU=$'\033[34m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YEL=""; C_BLU=""; C_OFF=""
fi

log()  { printf '%s[paper2md]%s %s\n' "$C_BLU" "$C_OFF" "$*"; }
ok()   { printf '%s[ ok ]%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
die()  { printf '%s[error]%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

COMMAND="install"
HARNESS=""
PYTHON_BIN=""
ASSUME_YES=0
COPY_SKILLS=1
LOCAL_CHECKOUT=""
EDITABLE=0

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      install|update|uninstall|verify) COMMAND="$1" ;;
      --harness) HARNESS="$2"; shift ;;
      --python)  PYTHON_BIN="$2"; shift ;;
      --prefix)  PREFIX="$2"; CHECKOUT_DIR="$PREFIX/src"; VENV_DIR="$PREFIX/venv"; shift ;;
      --no-skills) COPY_SKILLS=0 ;;
      --local)   LOCAL_CHECKOUT="$2"; shift ;;
      --editable) EDITABLE=1 ;;
      --yes)     ASSUME_YES=1 ;;
      -h|--help) usage ;;
      *) die "未知参数: $1（--help 查看用法）" ;;
    esac
    shift
  done
}

ask() { # ask <prompt> <default>
  [[ "$ASSUME_YES" == 1 ]] && return 0
  local answer
  printf '%s [%s] (y/N): ' "$1" "$2"
  read -r answer
  [[ "$answer" == "y" || "$answer" == "Y" ]]
}

# ── 1. 目标 harness 检测 ────────────────────────────────────────────
detect_harness() {
  local found=()
  [[ -d "$HOME/.claude/skills" ]] && found+=("claude")
  [[ -d "$HOME/.codex/skills" ]] && found+=("codex")
  [[ -d "$HOME/.gemini/skills" ]] && found+=("gemini")
  [[ -d "$HOME/.cursor" || -d "$PWD/.cursor" ]] && found+=("cursor")
  if [[ ${#found[@]} -gt 0 ]]; then
    printf '%s' "${found[0]}"
    [[ ${#found[@]} -gt 1 ]] && warn "检测到多个 harness（${found[*]}），默认选 ${found[0]}，用 --harness 指定"
  else
    printf 'none'
  fi
}

skills_dir_for() { # skills_dir_for <harness>
  case "$1" in
    claude)  printf '%s' "$HOME/.claude/skills" ;;
    codex)   printf '%s' "$HOME/.codex/skills" ;;
    gemini)  printf '%s' "$HOME/.gemini/skills" ;;
    cursor)  printf '%s' "$PWD/.cursor/skills" ;;
    none)    printf '' ;;
    *) die "未知 harness: $1" ;;
  esac
}

# ── 2. Python 选择（3.10–3.13）──────────────────────────────────────
pick_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    [[ -x "$PYTHON_BIN" ]] || die "--python 指定的解释器不存在: $PYTHON_BIN"
    echo "$PYTHON_BIN"
    return
  fi
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      local version
      version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
      case "$version" in
        3.10|3.11|3.12|3.13)
          echo "$candidate"
          return
          ;;
      esac
    fi
  done
  # uv 兜底：自动安装受支持版本
  if command -v uv >/dev/null 2>&1; then
    log "未找到 3.10–3.13 解释器，尝试用 uv 安装 Python 3.12..."
    uv python install 3.12 >/dev/null 2>&1 || die "uv 安装 Python 3.12 失败"
    echo "$(uv python find 3.12)"
    return
  fi
  die "需要 Python 3.10–3.13（可用 --python 指定，或安装 uv 后重试）"
}

# ── 3. 获取/更新源码 ────────────────────────────────────────────────
acquire_source() {
  if [[ -n "$LOCAL_CHECKOUT" ]]; then
    [[ -d "$LOCAL_CHECKOUT/src/paper2md" ]] || die "--local 目录不是 Paper2MD checkout: $LOCAL_CHECKOUT"
    CHECKOUT_DIR="$LOCAL_CHECKOUT"
    log "使用本地 checkout: $CHECKOUT_DIR"
    return
  fi
  if [[ "$COMMAND" == "update" && -d "$CHECKOUT_DIR/.git" ]]; then
    log "更新源码: $CHECKOUT_DIR"
    git -C "$CHECKOUT_DIR" fetch --quiet --depth 1 origin "$REPO_BRANCH" || warn "git fetch 失败，继续使用现有源码"
    git -C "$CHECKOUT_DIR" reset --quiet --hard "origin/$REPO_BRANCH" 2>/dev/null || true
  elif [[ -d "$CHECKOUT_DIR/.git" ]]; then
    log "复用已有源码: $CHECKOUT_DIR（update 子命令可拉取最新版）"
  else
    log "克隆源码: $CHECKOUT_DIR"
    mkdir -p "$(dirname "$CHECKOUT_DIR")"
    git clone --quiet --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$CHECKOUT_DIR" \
      || die "git clone 失败（检查网络或 --local 指定本地目录）"
  fi
  [[ -f "$CHECKOUT_DIR/pyproject.toml" ]] || die "checkout 缺少 pyproject.toml"
}

# ── 4. 安装 CLI ─────────────────────────────────────────────────────
install_cli() {
  local python_bin
  python_bin="$(pick_python)"
  log "使用 Python: $("$python_bin" --version 2>&1)"
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log "创建虚拟环境: $VENV_DIR"
    "$python_bin" -m venv "$VENV_DIR" || die "venv 创建失败"
  fi
  if [[ "$EDITABLE" == 1 ]]; then
    log "可编辑安装（--editable）：源码改动即时生效"
    "$VENV_DIR/bin/python" -m pip install --quiet --editable "$CHECKOUT_DIR" || die "pip install -e 失败"
  else
    log "pip install .（锁定依赖 pypdfium2/Pillow）"
    "$VENV_DIR/bin/python" -m pip install --quiet "$CHECKOUT_DIR" || die "pip install 失败"
  fi
  mkdir -p "$(dirname "$BIN_LINK")"
  ln -sf "$VENV_DIR/bin/paper2md" "$BIN_LINK"
  ok "CLI 已安装: $BIN_LINK"
}

# ── 5. 复制 skills ──────────────────────────────────────────────────
install_skills() {
  [[ "$COPY_SKILLS" == 0 ]] && { log "跳过 skills 安装（--no-skills）"; return; }
  [[ -z "$HARNESS" ]] && HARNESS="$(detect_harness)"
  [[ "$HARNESS" == "none" ]] && { warn "未检测到支持的 agent harness，跳过 skills（用 --harness 指定）"; return; }
  local target
  target="$(skills_dir_for "$HARNESS")"
  mkdir -p "$target"
  local count=0
  for skill in "${SKILL_DIRS[@]}"; do
    if [[ -d "$CHECKOUT_DIR/skills/$skill" ]]; then
      rm -rf "$target/$skill"
      cp -R "$CHECKOUT_DIR/skills/$skill" "$target/$skill"
      count=$((count + 1))
    else
      warn "checkout 中缺少 skill: $skill"
    fi
  done
  ok "已复制 $count 个 skills 到 $target"
  log "在 agent 中可通过 \$paper2md-install / \$paper2md-convert 等显式调用"
}

# ── 6. 验证 ─────────────────────────────────────────────────────────
verify() {
  local cli="$VENV_DIR/bin/paper2md"
  if [[ -x "$cli" ]]; then
    "$cli" --version
    "$cli" --help >/dev/null 2>&1 && ok "CLI 验证通过" || warn "CLI --help 异常"
  else
    die "未找到已安装的 CLI: $cli（先运行 install）"
  fi
  if command -v paper2md >/dev/null 2>&1; then
    ok "PATH 中可用: $(command -v paper2md)"
  else
    warn "paper2md 不在 PATH（把 $(dirname "$BIN_LINK") 加入 PATH）"
  fi
  local target
  target="$(skills_dir_for "${HARNESS:-$(detect_harness)}")"
  if [[ -n "$target" && -d "$target" ]]; then
    local n=0
    for skill in "${SKILL_DIRS[@]}"; do
      [[ -f "$target/$skill/SKILL.md" ]] && n=$((n + 1))
    done
    [[ "$n" -gt 0 ]] && ok "skills 发现: $n/4（$target）" || warn "未发现 skills（$target）"
  fi
}

# ── 7. 卸载 ─────────────────────────────────────────────────────────
uninstall() {
  if [[ -n "${HARNESS:-}" || "$ASSUME_YES" == 1 ]] || ask "卸载 skills、venv 与符号链接？（保留源码目录）" y; then
    local target
    target="$(skills_dir_for "${HARNESS:-$(detect_harness)}")"
    if [[ -n "$target" && -d "$target" ]]; then
      for skill in "${SKILL_DIRS[@]}"; do rm -rf "$target/$skill"; done
      ok "已移除 skills（$target）"
    fi
    rm -f "$BIN_LINK"
    rm -rf "$VENV_DIR"
    ok "已移除 CLI 与虚拟环境"
    log "源码保留在 $CHECKOUT_DIR；如需彻底删除: rm -rf $PREFIX"
  else
    log "取消卸载"
  fi
}

parse_args "$@"
case "$COMMAND" in
  install|update)
    acquire_source
    install_cli
    install_skills
    verify
    log "完成。重启 agent 会话后即可使用。"
    ;;
  verify)
    verify
    ;;
  uninstall)
    uninstall
    ;;
esac
