from typing import TypedDict, Optional, List


class AgentState(TypedDict):
    repo_url:        str
    token:           str
    openai_api_key:  str
    fork_owner:      str
    default_branch:  str
    fork_url:        str
    folder:          str
    context:         dict
    dockerfile:      str
    test_passed:     bool
    deploy_targets:  List[str]
    app_name:        str
    deploy_results:  dict
    pr_approved:     bool
    pr_url:          str
    deploy_approved: bool
    env_vars:        dict
    paused:          bool
    error:           Optional[str]
    current_step:    str
