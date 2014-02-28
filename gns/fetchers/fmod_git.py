import os
import subprocess
import shutil
import logging

from ulib import validators
import ulib.validators.common # pylint: disable=W0611
import ulib.validators.fs

from .. import service


##### Public constants #####
S_GIT = "git"

O_REPO_URL  = "repo-url"
O_REPO_DIR  = "repo-dir"
O_REVISIONS = "revisions"
O_PREFIX    = "prefix"


##### Private objects #####
_logger = logging.getLogger(__name__)


##### Private methods #####
def _shell_exec(command, **kwargs_dict):
    proc_stdout = subprocess.check_output(
        command.format(**kwargs_dict),
        env={ "LC_ALL": "C" },
        universal_newlines=True,
        shell=True,
    )
    _logger.debug("Command stdout:\n{}".format(proc_stdout))
    return proc_stdout

def _git_cleanup(rules_path, prefix, modules_list):
    for module_name in os.listdir(rules_path):
        if not module_name.startswith(prefix):
            continue
        if not module_name in modules_list:
            _logger.info("Removing the old module: %s", module_name)
            shutil.rmtree(os.path.join(rules_path, module_name))

def _git_update_rules(config_dict):
    _shell_exec("git --work-tree {repo} --git-dir {repo}/.git pull",
        repo=config_dict[S_GIT][O_REPO_DIR],
    )

    rules_path = config_dict[service.S_CORE][service.O_RULES_DIR]
    prefix = config_dict[S_GIT][O_PREFIX]

    modules_list = []
    commits_list = _shell_exec("git --work-tree {repo} --git-dir {repo}/.git log -n {limit} --pretty=format:%H",
        repo=config_dict[S_GIT][O_REPO_DIR],
        limit=config_dict[S_GIT][O_REVISIONS],
    ).strip().split("\n")
    assert len(commits_list) > 0
    for commit in commits_list:
        module_name = prefix + commit
        modules_list.append(module_name)

        module_path = os.path.join(rules_path, module_name)
        if os.path.exists(module_path):
            continue

        tmp_path = os.path.join(rules_path, "." + module_name)
        if os.path.exists(tmp_path):
            shutil.rmtree(tmp_path)
        os.mkdir(tmp_path)

        _logger.info("Checkout %s --> %s", commit, module_path)
        _shell_exec("git --work-tree {repo} --git-dir {repo}/.git archive {commit} | tar -x -C {tmp}",
            repo=config_dict[S_GIT][O_REPO_DIR],
            commit=commit,
            tmp=tmp_path,
        )
        os.rename(tmp_path, module_path)

    _git_cleanup(rules_path, prefix, modules_list)

    return prefix + commits_list[0]


##### Public constants #####
CONFIG_MAP = {
    S_GIT: {
        O_REPO_URL:  ("http://example.com", str),
        O_REPO_DIR:  ("/tmp",               lambda arg: validators.fs.valid_accessible_path(arg + "/.")),
        O_REVISIONS: (10,                   lambda arg: validators.common.valid_number(arg, 1)),
        O_PREFIX:    ("git_",               str),
    },
}

FETCHERS_MAP = {
    "git": _git_update_rules,
}

