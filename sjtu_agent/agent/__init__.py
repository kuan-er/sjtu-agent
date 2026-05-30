"""sjtu_agent/agent/__init__.py — 统一导出接口。"""
from sjtu_agent.agent.prompts import SYSTEM_PROMPT, _TOOL_LABELS, build_system_prompt
from sjtu_agent.agent.tools import (
    TOOLS, run_tool,
    tool_check_setup, tool_get_ddls, tool_get_next_lab, tool_get_all,
    tool_get_schedule, tool_query_grades,
    tool_add_reminder, tool_list_reminders, tool_remove_reminder,
    tool_read_emails, tool_search_emails, tool_send_email,
    tool_download_assignments, tool_list_assignment_files, tool_read_assignment_file,
    tool_parse_local_file, tool_parse_local_files, tool_install_parse_backend,
    tool_search_campus, tool_browse_mysjtu,
    tool_save_credentials, tool_login_platform,
    tool_setup_canvas, tool_setup_shuiyuan,
    tool_add_mcp_server, tool_add_skill, tool_create_skill, tool_list_skills, tool_manage_skill,
    tool_setup_telegram, tool_setup_wechat, tool_setup_feishu, tool_setup_qq,
    tool_qq_add_user, tool_qq_list_users, tool_qq_remove_user,
    tool_execute_python, tool_get_user_profile, tool_update_user_profile,
    tool_list_canvas_assignments, tool_submit_canvas_assignment,
    tool_refresh_mysjtu_catalog,
    _fetch_ddls_parallel, _ddl_cache_get,
)
from sjtu_agent.agent.runner import (
    Spinner, _make_client, _is_anthropic_model, _anthropic_tools,
    _run_one_turn, _run_one_turn_openai, _run_one_turn_anthropic,
    _stream_with_think_tags, _ANSI_OK,
)
from sjtu_agent.agent.chat_loop import (
    load_agent_config, setup_agent_config, chat_loop, main,
    _prefetch_ddls_background, _check_for_updates, _UPDATE_AVAILABLE,
)
from sjtu_agent.extensions.registry import get_available_tools, run_registered_tool

# Backward-compatible dynamic dispatcher. Existing integrations import
# agent.run_tool; routing it through the registry lets MCP tools participate.
run_tool = run_registered_tool
