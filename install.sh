#!/bin/bash

export GG_GIT_HELPERS=${PWD}

fail() {
    echo $* >&2

    exit 1
}
try () {
    echo $*
    $* || fail $*
}

install_gg_cli() {
    echo "Install gg CLI tool"
    if command -v uv >/dev/null 2>&1; then
        try uv tool install --reinstall -e "${GG_GIT_HELPERS}"
    else
        echo "WARNING: uv not found. Install uv first, then run:"
        echo "  uv tool install --reinstall -e ${GG_GIT_HELPERS}"
    fi
}

install_skill() {
    local dest=~/.claude/skills/gg
    echo "Install the gg skill for Claude Code"
    try mkdir -p ~/.claude/skills
    if [ -L "$dest" ]; then
        try rm -f "$dest"
    elif [ -e "$dest" ]; then
        fail "$dest exists and is not a symlink; move it away first"
    fi
    try ln -s "${GG_GIT_HELPERS}/skills/gg" "$dest"
}

usage() {
    cat <<EOF
Usage: ./install.sh [--gg | --skill | -h]

Run it from the git-helpers checkout.

  (no option)  link ~/.bashrc.gitgo, install the gg CLI, and print the
               snippets to add to ~/.bashrc and ~/.gitconfig
  --gg         only (re)install the gg CLI (uv tool install -e)
  --skill      only link skills/gg to ~/.claude/skills/gg: the Claude Code
               skill that teaches agents about gg
  -h, --help   show this help
EOF
}

case "$1" in
    -h|--help) usage; exit 0 ;;
    --gg) install_gg_cli; exit 0 ;;
    --skill) install_skill; exit 0 ;;
    "") ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
esac

echo "Install .bashrc.gitgo"
try rm -f ~/.bashrc.gitgo
try ln -s ${GG_GIT_HELPERS}/bashrc.gitgo ~/.bashrc.gitgo

echo ""
install_gg_cli

echo -e "\n\n"
echo -e "!!!Add the below into your $HOME/.bashrc \n\n"

cat <<EOF
################## GIT-HELPERS ################
# vgit show
export GG_GIT_HELPERS=$GG_GIT_HELPERS
. \${GG_GIT_HELPERS}/bashrc.vgit
EOF

echo -e "\n\n"
echo -e "Add the below to your .gitconfig\n\n"

cat <<EOF
################## GIT-HELPERS ################
[gc]
	reflogExpire = 2 years

[merge]
	conflictstyle = diff3

[include]
  path = ${GG_GIT_HELPERS}/gitconfig.go
  path = ${GG_GIT_HELPERS}/gitconfig.tree
  path = ${GG_GIT_HELPERS}/gitconfig.alias

EOF

echo "If you're on mac - make sure gsed is in PATH"
echo "To teach Claude Code agents about gg, run: ./install.sh --skill"
