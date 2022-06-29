#!/bin/bash

export OL_GIT_HELPERS=${PWD}

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
try ln -s ${OL_GIT_HELPERS}/bashrc.gitgo ~/.bashrc.gitgo

echo -e "\n\n"
echo -e "!!!Add the below into your $HOME/.bashrc \n\n"

cat <<EOF
################## GIT-HELPERS ################
# vgit show
export OL_GIT_HELPERS=$OL_GIT_HELPERS
. \${OL_GIT_HELPERS}/bashrc.vgit
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
  path = ${OL_GIT_HELPERS}/gitconfig.go
  path = ${OL_GIT_HELPERS}/gitconfig.tree
  path = ${OL_GIT_HELPERS}/gitconfig.alias

EOF

echo "If you're on mac - make sure gsed is in PATH"
