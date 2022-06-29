# vim:ft=gitconfig
#
# Usage from ~/.gitconfig:
# [include]
#     path = /path/to/gitconfig.go
#
# Assumes that ~/.bashrc.gitgo is a symmlink to bashrc.gitgo from the same
# repo.
#
# For workflow see bashrc.gitgo comments.
#
[alias]
	branchname = "!. ~/.bashrc.gitgo; git_branchname"
	summary = "!. ~/.bashrc.gitgo; git_summary"

	# See workflow in ~/.bashrc.gitgo
	gowork = "!. ~/.bashrc.gitgo; git_gowork"
	gopull = "!. ~/.bashrc.gitgo; git_gopull"
	gostatus = !sh -c 'git -c color.ui=always branch -vv |grep --color=never ^*'

	gopr = "!source ~/.bashrc.gitgo; git_gopr"
	gorbt = "!. ~/.bashrc.gitgo; git_gorbt"

        gopublish =  "!. ~/.bashrc.gitgo; git_gopublish"

	gopush = "!. ~/.bashrc.gitgo; git_gopush"
	goclose = "!. ~/.bashrc.gitgo; git_goclose"
	godiscard = "!. ~/.bashrc.gitgo; git_godiscard"

	golog = "!. ~/.bashrc.gitgo; git_golog"
	goshow = "!. ~/.bashrc.gitgo; git_goshow"


	gosyncfrom = "!. ~/.bashrc.gitgo; git_gosyncfrom"
	gosyncto = "!. ~/.bashrc.gitgo; git_gosyncto"
