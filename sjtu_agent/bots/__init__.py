"""Shared conversation-core for the SJTU platform bots.

Each platform script (telegram/wechat/qq/feishu) keeps only its transport,
media, and command plumbing; the stateless turn logic lives in `_core`.
"""
