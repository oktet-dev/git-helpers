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

echo "Install .bashrc.gitgo"
try rm -f ~/.bashrc.gitgo
try ln -s ${GG_GIT_HELPERS}/bashrc.gitgo ~/.bashrc.gitgo

echo ""
echo "Install gg CLI tool"
if command -v uv >/dev/null 2>&1; then
    try uv tool install --reinstall -e "${GG_GIT_HELPERS}"
else
    echo "WARNING: uv not found. Install uv first, then run:"
    echo "  uv tool install --reinstall -e ${GG_GIT_HELPERS}"
fi

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
